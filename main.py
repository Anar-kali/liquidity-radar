"""
Liquidity Radar — the conductor.

Usage:
    python main.py --mode fast          # exchanges + news
    python main.py --mode news          # news only
    python main.py --mode slow          # SEBI DRHP
    python main.py --mode fast --dry     # print alerts to terminal, send nothing
    python main.py --test-telegram       # send one test message and exit

Pipeline for a run:
    1. Fetch items for the mode.
    2. Drop items already in the database (dedup on source id / URL).
    3. Classify the new ones with Haiku, in batches of 25.
    4. Confirmed negatives go to the suppression log.
    5. Everything else goes through clustering, which decides whether to fire
       a NEW alert, a follow-up UPDATE, or attach silently.
"""

import argparse
import re
from collections import Counter

import classify
import cluster
import config
import db
import feedback
import notify
import sizing
import sources


_RULE_NUMBER_RE = re.compile(r"^\s*(?:rule\s*)?#?(\d+)", re.IGNORECASE)


def _rule_number(reason):
    """
    Extract a stable 'Rule N' label from the classifier's negative_reason.

    Anchored to the START of the string on purpose. Haiku consistently writes
    "Rule 9: Earnings result..." — the digit is directly followed by a colon,
    so a naive "any whitespace-split token that isdigit()" scan (the previous
    approach) never matches "9:" and falls through to scanning the REST of
    the free-text explanation, where it can latch onto a completely unrelated
    bare number later in the sentence (an amount, a percentage) and report a
    nonexistent "Rule 38". Anchoring to the first number at the start of the
    string, regardless of what punctuation follows it, fixes both failure
    modes at once.
    """
    if not reason:
        return "Rule ?"
    m = _RULE_NUMBER_RE.match(str(reason))
    return f"Rule {m.group(1)}" if m else "Rule ?"


def run(mode, dry, limit=None):
    db.init_db()

    # Pick up any Telegram button presses (Useful / Already knew / Noise)
    # from since the last run, at the start of every run.
    feedback.poll_feedback(dry=dry)

    raw = sources.fetch_for_mode(mode)
    print(f"[main] fetched {len(raw)} items in mode '{mode}'")

    # Dedup against what we've already seen, and record the new ones.
    fresh = []
    for item in raw:
        if not item.get("id"):
            continue
        if db.item_seen(item["id"]):
            continue
        db.add_item(item)
        fresh.append(item)
    print(f"[main] {len(fresh)} new items after dedup")

    if not fresh:
        return

    suppressed = [0]  # boxed so the local helper can mutate it

    def suppress(item, rule, r):
        db.add_suppressed(
            title=item.get("title", ""),
            url=item.get("url", ""),
            rule=rule,
            amount_cr=r.get("amount_cr"),
            amount_raw=r.get("amount_raw"),
        )
        suppressed[0] += 1

    # ---- STAGE 1: Haiku, reject confirmed noise (high recall) ----
    stage1 = classify.classify_all(fresh)
    survivors = []          # items that go to the precision check
    sizing_by_id = {}       # item id -> sizing result, carried into stage 2
    for item, r1 in stage1:
        if r1["confirmed_negative"]:
            rule = _rule_number(r1.get("negative_reason"))
            # Don't let a "under threshold" drop kill a deal whose STATED size
            # actually parses to >= threshold — let stage 2 judge it instead.
            if rule == "Rule 8":
                stated = classify.stated_cr_max(
                    r1.get("amount_raw"), item.get("title"), item.get("description"))
                if stated is not None and stated >= config.THRESHOLD_CR:
                    survivors.append(item)
                    sizing_by_id[item["id"]] = {"size_source": "stated"}
                    continue
            suppress(item, rule, r1)
            continue

        # Stated amount: small ones die here; large ones proceed.
        if r1["amount_cr"] is not None:
            if r1["amount_cr"] < config.THRESHOLD_CR:
                suppress(item, "Rule 8", r1)
                continue
            survivors.append(item)
            sizing_by_id[item["id"]] = {"size_source": "stated"}
            continue

        # No stated amount: resolve size (listed -> compute / plausibility).
        text = f"{item.get('title', '')} {item.get('description', '')}"
        sz = sizing.resolve_size(r1.get("company") or item.get("title", ""), text)
        src = sz.get("size_source")
        if src == "computed" and sz["amount_cr"] < config.THRESHOLD_CR:
            suppress(item, "Rule 8", {
                "amount_cr": sz["amount_cr"],
                "amount_raw": f"{sz['pct']:g}% x mcap ~Rs {sz['mcap_cr']:.0f}cr"})
            continue
        if src == "mcap_plausible" and sz["mcap_cr"] < config.MCAP_PLAUSIBLE_MIN:
            suppress(item, "Rule 8", {
                "amount_cr": None,
                "amount_raw": f"mcap ~Rs {sz['mcap_cr']:.0f}cr < {config.MCAP_PLAUSIBLE_MIN}"})
            continue
        sizing_by_id[item["id"]] = sz
        survivors.append(item)

    print(f"[main] stage1: {suppressed[0]} suppressed, {len(survivors)} to precision-check")

    # ---- STAGE 2: precision confirm + size band (Haiku) ----
    alerts = []
    mix = Counter()
    for item, r2 in classify.precision_classify(survivors):
        if not r2["qualify"]:
            suppress(item, "Rule P", r2)
            continue

        sz = sizing_by_id.get(item["id"], {})
        src = sz.get("size_source")

        # Fill in a computed amount if stage 2 found no figure of its own.
        if r2["amount_cr"] is None and src == "computed":
            r2["amount_cr"] = sz["amount_cr"]
            r2["amount_raw"] = (f"{sz['pct']:g}% x mcap ~Rs {sz['mcap_cr']:.0f}cr "
                                f"= ~Rs {sz['amount_cr']:.0f}cr")

        # Deterministic small-amount gate on whatever amount we now have.
        if r2["amount_cr"] is not None and r2["amount_cr"] < config.THRESHOLD_CR:
            suppress(item, "Rule 8", r2)
            continue

        # Band gate — only for genuinely unlisted / unresolved items.
        if r2["amount_cr"] is None and src is None:
            if (r2.get("size_band") == "UNDER_100"
                    and (r2.get("size_basis") or "").strip().lower() != "no information"):
                suppress(item, "Rule S", r2)
                continue

        # Decide how the size will be shown, and record the mix.
        if r2["amount_cr"] is not None:
            r2["size_source"] = "computed" if src == "computed" else "stated"
        elif src == "mcap_plausible":
            r2["size_source"] = "mcap_plausible"
        elif r2.get("size_band") in ("100_TO_500", "500_TO_2000", "OVER_2000"):
            r2["size_source"] = "band"
        else:
            r2["size_source"] = "unknown"
        mix[r2["size_source"]] += 1

        new_alerts = cluster.process(item, r2)
        alerts.extend(new_alerts)

        # v3 Change B: feed the salami-slice aggregator. Recorded only when
        # cluster.process() actually alerted (new deal or amount UPDATE) —
        # not per raw article — so a deal repeated across five outlets
        # doesn't get counted five times toward the rolling 90-day sum.
        if new_alerts and r2.get("individuals") and r2.get("amount_cr") is not None:
            company_key = cluster.deal_key(r2.get("company") or "")
            for individual in r2["individuals"]:
                pk, canonical_name = cluster.resolve_person_key(
                    individual, company_key, config.AGGREGATION_WINDOW_DAYS)
                if pk:
                    db.add_individual_sale(
                        pk, canonical_name, r2.get("company") or "", company_key,
                        db.now_iso()[:10], r2["amount_cr"], "news")

    print(f"[main] {suppressed[0]} suppressed total, {len(alerts)} alerts to send")
    if mix:
        print("[main] size_source mix: " + ", ".join(f"{k}={v}" for k, v in mix.items()))

    # Optional throttle (useful for testing, or to avoid a flood on a cold
    # start). Deals are still recorded; only the notifications are capped.
    if limit is not None and len(alerts) > limit:
        print(f"[main] --limit {limit}: sending {limit} of {len(alerts)} alerts")
        alerts = alerts[:limit]

    for alert in alerts:
        if dry:
            print("\n" + "=" * 60)
            print(notify.format_alert(alert))
        else:
            notify.send_alert(alert)


def main():
    parser = argparse.ArgumentParser(description="Liquidity Radar")
    parser.add_argument("--mode", choices=["fast", "news", "slow", "auto"])
    parser.add_argument("--dry", action="store_true",
                        help="print alerts to the terminal instead of Telegram")
    parser.add_argument("--test-telegram", action="store_true",
                        help="send one test message and exit")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap how many alerts are sent this run (testing / "
                             "anti-flood). Deals are still recorded.")
    args = parser.parse_args()

    if args.test_telegram:
        notify.send_test()
        return

    if not args.mode:
        parser.error("--mode is required (fast | news | slow) unless --test-telegram")

    run(args.mode, args.dry, args.limit)


if __name__ == "__main__":
    main()
