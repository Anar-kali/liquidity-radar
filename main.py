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

import classify
import cluster
import config
import db
import notify
import sources


def _rule_number(reason):
    """Extract a stable 'Rule N' label from the classifier's negative_reason."""
    if not reason:
        return "Rule ?"
    for token in str(reason).replace(".", " ").split():
        if token.isdigit():
            return f"Rule {token}"
    return "Rule ?"


def run(mode, dry, limit=None):
    db.init_db()

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

    classified = classify.classify_all(fresh)

    alerts = []
    suppressed_count = 0
    for item, result in classified:
        if result["confirmed_negative"]:
            rule = _rule_number(result.get("negative_reason"))

            # Safety net for Haiku's divide-by-10 slip: never let a
            # "under threshold" (rule 8) suppression drop a deal whose STATED
            # size actually parses to >= the threshold. A missed big deal is
            # the one error we cannot afford, so we err toward keeping it.
            if rule == "Rule 8":
                stated = classify.stated_cr_max(
                    result.get("amount_raw"),
                    item.get("title"),
                    item.get("description"),
                )
                if stated is not None and stated >= config.THRESHOLD_CR:
                    print(
                        f"[main] rule-8 override: stated {stated:g}cr >= "
                        f"{config.THRESHOLD_CR}cr, keeping: "
                        f"{item.get('title', '')[:60]}"
                    )
                    # Only fill the amount if the model gave none; don't
                    # clobber a figure the reconcile step already handled.
                    if result.get("amount_cr") is None:
                        result["amount_cr"] = stated
                    alerts.extend(cluster.process(item, result))
                    continue

            db.add_suppressed(
                title=item.get("title", ""),
                url=item.get("url", ""),
                rule=rule,
                amount_cr=result.get("amount_cr"),
                amount_raw=result.get("amount_raw"),
            )
            suppressed_count += 1
            continue
        alerts.extend(cluster.process(item, result))

    print(f"[main] {suppressed_count} suppressed, {len(alerts)} alerts to send")

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
    parser.add_argument("--mode", choices=["fast", "news", "slow"])
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
