"""
Liquidity Radar — the conductor.

Usage:
    python main.py --mode fast          # exchanges + news
    python main.py --mode news          # news only
    python main.py --mode slow          # SEBI DRHP
    python main.py --mode fast --dry     # print alerts to terminal, send nothing
    python main.py --test-telegram       # send one test message and exit

Pipeline for a run (v4 Part 1 — see SPEC-v4-upgrade.md):
    1. Fetch items for the mode.
    2. Drop items already in the database (dedup on source id / URL).
    3. Structural blocklist: drop liveblogs/slideshows/etc by document type.
    4. Title dedup: drop near-duplicate headlines (Google News URL rotation).
    5. BSE market-cap gate: drop a BSE filing whose company resolves to a
       market cap under config.BSE_MCAP_MIN_CR — not worth alerting on
       regardless of what the filing says. No-op for non-BSE items.
    6. Pre-API amount gate: regex-suppress a clearly-small stated deal before
       spending an API call on it.
    7. Stage 1 (Haiku, slim boolean+rule schema): reject confirmed noise.
       SEBI DRHP items skip this stage entirely (Change 5).
    8. Stage 2 (Haiku, full extraction): positively confirm + size the deal.
    9. Everything that qualifies goes through clustering, which decides
       whether to fire a NEW alert, a follow-up UPDATE, or attach silently.

Every counter above is recorded to db.funnel_runs so the daily digest can
show where volume goes.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone

import classify
import cluster
import config
import db
import feedback
import filters
import notify
import sizing
import sources


def _rule_label(rule_number):
    return f"Rule {rule_number}" if rule_number is not None else "Rule ?"


_FAIL_WARN_KEY = "classify_fail_warned_at"
_FAILING_KEY = "classify_failing"


def _report_classifier_health(fail_reasons, judged_ok, dry):
    """
    One message when the classifier goes down, one when it comes back.

    Deliberately throttled: the failure mode this guards against is volume, so
    a warning per run would be a slower version of the same problem. The held
    items are safe in classify_retry either way — the message is for the human,
    not the pipeline.

    `judged_ok` is how many items got a real verdict this run. Recovery is
    only announced when something actually succeeded — a run with nothing to
    classify proves nothing, and would otherwise send a false all-clear.
    """
    was_failing = db.get_state(_FAILING_KEY) == "1"

    if not fail_reasons:
        if was_failing and judged_ok:
            db.set_state(_FAILING_KEY, "0")
            db.set_state(_FAIL_WARN_KEY, "")
            print("[main] classifier recovered")
            if not dry:
                notify.send("✅ *Liquidity Radar*: classifier is back. "
                            "Held items are being reprocessed.")
        return

    held = db.retry_queue_size()
    # Distinct reasons only — 40 identical TypeErrors say nothing 1 doesn't.
    reasons = sorted(set(fail_reasons))
    print(f"[main] classifier failing ({len(fail_reasons)} batched items held, "
          f"{held} in queue): {reasons[0]}")
    db.set_state(_FAILING_KEY, "1")

    last = db.get_state(_FAIL_WARN_KEY) or ""
    if last:
        try:
            due = datetime.fromisoformat(last) + timedelta(
                minutes=config.CLASSIFY_FAIL_WARN_COOLDOWN_MIN)
            if datetime.now(timezone.utc) < due:
                return  # already told them recently
        except ValueError:
            pass  # unparseable timestamp — treat as never warned

    db.set_state(_FAIL_WARN_KEY, db.now_iso())
    if dry:
        return
    detail = "\n".join(f"• `{r[:180]}`" for r in reasons[:3])
    notify.send(
        f"⚠️ *Liquidity Radar: classifier unavailable*\n\n"
        f"{held} item(s) are held and will be re-checked automatically when "
        f"it recovers. *No alerts are being missed silently, and nothing "
        f"unclassified is being sent.*\n\n{detail}"
    )


def _stage1_fallback(exc):
    """Last-resort result if stage 1 raises entirely, beyond classify.py's own
    per-batch handling. Flagged as a failure, so the item is parked for retry
    rather than treated as having passed."""
    return classify.failed_result1(exc)


def _stage2_fallback(exc):
    """Last-resort result if stage 2 raises entirely. See _stage1_fallback."""
    return classify.failed_result2(exc)


def run(mode, dry, limit=None):
    db.init_db()
    db.prune_item_queries()

    # Pick up any Telegram button presses (Useful / Already knew / Noise)
    # from since the last run, at the start of every run.
    feedback.poll_feedback(dry=dry)

    raw = sources.fetch_for_mode(mode)
    print(f"[main] fetched {len(raw)} items in mode '{mode}'")

    funnel = {
        "mode": mode, "fetched": len(raw), "already_seen": 0,
        "structural_dropped": 0, "title_deduped": 0, "bse_mcap_gated": 0,
        "pre_api_gated": 0, "reached_stage1": 0, "reached_stage2": 0, "alerted": 0,
    }

    # Dedup against what we've already seen. Query attribution (v4 Change 7)
    # is recorded for EVERY raw item, even ones the id-based dedup below
    # drops, so query overlap is fully visible — not just whichever query
    # happened to surface an item first.
    candidates = []
    for item in raw:
        if not item.get("id"):
            continue
        q = item.get("source_query")
        if q:
            db.add_item_query(item["id"], q)
        if db.item_seen(item["id"]):
            funnel["already_seen"] += 1
            continue
        candidates.append(item)

    # v4 Change 9: shadow mode. Changes 1, 3, and 4 below only ever ACTUALLY
    # drop an item when config.PREFILTER_MODE == "enforce" (a GitHub Actions
    # repo variable, default "shadow"). In shadow mode every filter still
    # computes and logs its decision — to prefilter_shadow, or to
    # title_dedup_log for Change 4 — but the item passes through regardless,
    # so a trial week costs nothing extra and shadow_report.py can show
    # exactly what enforcing would have removed before it's ever real.
    def apply_prefilter(item, fires, filter_name, reason, funnel_key, log_shadow=True):
        if not fires:
            return False
        funnel[funnel_key] += 1
        if config.PREFILTER_MODE != "enforce":
            if log_shadow:
                db.add_prefilter_shadow(filter_name, item.get("title", ""),
                                         item.get("url", ""), reason)
            return False
        return True

    # Structural blocklist (v4 Change 3) — document-type filter, before
    # anything else. No suppression-log entry when enforced, just a counter:
    # this isn't a content judgment that needs auditing later.
    kept = []
    for item in candidates:
        fires = filters.is_structural_noise(item)
        if apply_prefilter(item, fires, "structural", "document-type match",
                            "structural_dropped"):
            continue
        kept.append(item)
    candidates = kept

    # Title dedup (v4 Change 4) — Google News rotates article URLs, so the
    # same story looks new to the id-based dedup above. Every candidate is
    # still recorded to `items` (with title_norm) so history stays complete
    # and future runs can dedup against it too. Every DROP (shadow or real)
    # is logged to title_dedup_log unconditionally — unlike a clustering
    # merge, a dedup drop leaves no other trace, so this is the only way to
    # ever discover a wrong one.
    recent_norms = db.recent_title_norms(hours=config.TITLE_DEDUP_WINDOW_HOURS)
    recent = [(n, filters.distinguishing_tokens(n)) for n in recent_norms]
    fresh = []
    for item in candidates:
        title_norm = filters.normalise_title(item.get("title", ""))
        tokens = filters.distinguishing_tokens(title_norm)
        if filters.is_title_dedup_exempt(item):
            # Exchange/regulator filings: unique document URL, generic
            # category title. id dedup already handles them exactly.
            is_dup, matched_norm, sim = False, None, None
        else:
            is_dup, matched_norm, sim = filters.title_dedup_decision(title_norm, tokens, recent)
        item["title_norm"] = title_norm
        db.add_item(item)
        if is_dup:
            db.add_title_dedup_log(item.get("title", ""), item.get("url", ""),
                                    matched_norm, sim)
        # log_shadow=False: title_dedup_log above already covers this drop's
        # audit trail, so prefilter_shadow doesn't need a redundant row.
        if apply_prefilter(item, is_dup, "title_dedup",
                            f"matched '{matched_norm}' (sim={sim:.2f})" if is_dup else "",
                            "title_deduped", log_shadow=False):
            continue
        recent.append((title_norm, tokens))
        fresh.append(item)

    print(f"[main] {len(fresh)} new items after dedup "
          f"(already-seen {funnel['already_seen']}, structural "
          f"-{funnel['structural_dropped']}, title dedup -{funnel['title_deduped']}, "
          f"PREFILTER_MODE={config.PREFILTER_MODE})")

    # Nothing new AND nothing held — only then is there genuinely no work.
    # Held items must still get their retry on a quiet run, or an outage that
    # coincided with a slow news hour would strand them until the age cap.
    if not fresh and not db.retry_queue_size():
        db.add_funnel_run(funnel)
        return

    suppressed = [0]  # boxed so the local helper can mutate it

    def suppress(item, rule, r, gate="model"):
        db.add_suppressed(
            title=item.get("title", ""),
            url=item.get("url", ""),
            rule=rule,
            amount_cr=r.get("amount_cr"),
            amount_raw=r.get("amount_raw"),
            gate=gate,
        )
        suppressed[0] += 1

    # BSE market-cap gate — a BSE filing's company name usually resolves to a
    # real, cached market cap (sizing.py), unlike free-text news, so this
    # suppresses on company size directly rather than deal-value regex. A
    # no-op for anything not sourced from BSE, and for a BSE company that
    # doesn't resolve to a listed ticker (recall bias: unknown passes).
    mcap_survivors = []
    for item in fresh:
        mcap, ticker = filters.bse_market_cap(item)
        fires = mcap is not None and mcap < config.BSE_MCAP_MIN_CR
        reason = f"BSE company {ticker} mcap ~Rs {mcap:.0f}cr < {config.BSE_MCAP_MIN_CR}cr" if fires else ""
        if apply_prefilter(item, fires, "bse_mcap", reason, "bse_mcap_gated"):
            suppress(item, "Rule M", {"amount_cr": None, "amount_raw": reason}, gate="pre-api")
            continue
        mcap_survivors.append(item)
    fresh = mcap_survivors

    # Pre-API amount gate (v4 Change 1) — regex-only, no model call. Suppress
    # only when the LARGEST proximity-gated rupee figure found in the raw
    # text is confidently under threshold; never on a smaller figure when a
    # larger one is also present ("sells 5% stake for Rs150cr in a Rs2,000cr
    # deal" survives).
    pre_survivors = []
    for item in fresh:
        stated = filters.pre_api_stated_cr(item)
        fires = stated is not None and stated < config.THRESHOLD_CR
        reason = f"regex: Rs {stated:g}cr (proximity-gated)" if fires else ""
        if apply_prefilter(item, fires, "pre_api_amount", reason, "pre_api_gated"):
            suppress(item, "Rule 8", {"amount_cr": stated, "amount_raw": reason},
                     gate="pre-api")
            continue
        pre_survivors.append(item)
    fresh = pre_survivors

    # SEBI DRHP filings (v4 Change 5) skip stage 1 entirely — a DRHP is by
    # definition a company going public, which stage 1 would never mark
    # confirmed-negative, so the call is wasted every time.
    sebi_direct = [item for item in fresh if item.get("source") == "SEBI"]
    # ---- SAFETY VALVE: re-admit items a previous run couldn't classify ----
    # These already cleared the prefilters, so each re-enters at exactly the
    # stage that failed — a stage-2 failure never pays for stage 1 again.
    expired = db.expire_retries(config.CLASSIFY_RETRY_MAX_ATTEMPTS,
                                config.CLASSIFY_RETRY_MAX_AGE_HOURS)
    for row in expired:
        # Audit trail rather than a silent drop: if the classifier stayed down
        # long enough to strand an item, that fact should be searchable later.
        suppress(row, "Rule X", {"amount_cr": None,
                                 "amount_raw": f"unclassified after {row['attempts']} attempts"},
                 gate="retry-expired")
    if expired:
        print(f"[main] gave up on {len(expired)} items the classifier never judged")

    retry1 = db.pending_retries("stage1")
    retry2 = db.pending_retries("stage2")
    # Only these ids can possibly need clearing once a verdict lands, so a
    # normal run never pays for a DELETE-that-matches-nothing per item.
    retry_ids = {i["id"] for i in retry1} | {i["id"] for i in retry2}
    if retry1 or retry2:
        print(f"[main] re-admitting {len(retry1)} stage-1 + {len(retry2)} "
              f"stage-2 items held from earlier runs")

    # Every failure seen this run, so one warning can be sent at the end
    # instead of one alert per unclassified item.
    fail_reasons = []
    judged_ok = [0]  # real verdicts this run — proof the API actually works

    def hold(item, stage, r):
        db.enqueue_retry(item["id"], stage)
        reason = r.get("failure_reason")
        if reason:
            fail_reasons.append(reason)

    to_classify = [item for item in fresh if item.get("source") != "SEBI"] + retry1
    funnel["reached_stage1"] = len(to_classify)

    # ---- STAGE 1: Haiku, reject confirmed noise (high recall) ----
    # v4 Change 6: failure isolation. classify.classify_all() already fails
    # open per-batch internally; this outer guard is a last-resort net for
    # anything else that could go wrong, so one broken component never aborts
    # the whole run.
    try:
        stage1 = classify.classify_all(to_classify)
    except Exception as exc:  # noqa: BLE001
        print(f"[main] stage1 failed entirely, holding all for retry: {exc}")
        stage1 = [(item, _stage1_fallback(exc)) for item in to_classify]

    survivors = list(sebi_direct) + retry2  # items that go to the precision check
    for item, r1 in stage1:
        # A failed API call is not a verdict — park it, don't judge it. It is
        # neither suppressed (which would lose a possible deal) nor passed on
        # (which, pre-2026-08-21, meant alerting raw unclassified headlines).
        if r1.get("classify_failed"):
            hold(item, "stage1", r1)
            continue
        judged_ok[0] += 1
        if item["id"] in retry_ids:  # got a real verdict at last; stop retrying
            db.clear_retry(item["id"])
        if r1["confirmed_negative"]:
            rule = _rule_label(r1.get("rule_number"))
            # Don't let a "under threshold" drop kill a deal whose STATED size
            # actually parses to >= threshold from the raw text — let stage 2
            # judge it instead. (Stage 1 no longer extracts amount_raw itself
            # under the slim v4 schema, so this regexes the item text
            # directly — the same regex the pre-API gate above uses.)
            if rule == "Rule 8":
                # Deliberately the general (non-proximity-gated) figure
                # reader here, not filters.pre_api_stated_cr — this is a
                # rescue check, not a suppression, so being generous only
                # costs an extra stage-2 call, never a wrongly-dropped lead.
                stated = classify.stated_cr_max(item.get("title"), item.get("description"))
                if stated is not None and stated >= config.THRESHOLD_CR:
                    survivors.append(item)
                    continue
            suppress(item, rule, {"amount_cr": None, "amount_raw": None})
            continue
        survivors.append(item)

    funnel["reached_stage2"] = len(survivors)
    print(f"[main] stage1: {suppressed[0]} suppressed, {len(survivors)} to precision-check")

    # ---- STAGE 2: precision confirm + size band (Haiku) ----
    try:
        stage2 = classify.precision_classify(survivors)
    except Exception as exc:  # noqa: BLE001
        print(f"[main] stage2 failed entirely, holding all for retry: {exc}")
        stage2 = [(item, _stage2_fallback(exc)) for item in survivors]

    alerts = []
    mix = Counter()
    for item, r2 in stage2:
        # As in stage 1: hold, don't judge. Checked before `qualify` because a
        # failed result carries no meaningful qualify value.
        if r2.get("classify_failed"):
            hold(item, "stage2", r2)
            continue
        judged_ok[0] += 1
        if item["id"] in retry_ids:
            db.clear_retry(item["id"])
        if not r2["qualify"]:
            suppress(item, "Rule P", r2)
            continue

        # Resolve size: stage 2's own stated figure first; otherwise try
        # listed-company market-cap inference (v4: this now runs here, once,
        # using stage 2's own extracted company — stage 1 no longer extracts
        # a company under the slim schema, so there's nothing to reuse from
        # an earlier pass).
        src = None
        if r2["amount_cr"] is None:
            text = f"{item.get('title', '')} {item.get('description', '')}"
            sz = sizing.resolve_size(r2.get("company") or item.get("title", ""), text)
            src = sz.get("size_source")
            if src == "computed":
                if sz["amount_cr"] < config.THRESHOLD_CR:
                    suppress(item, "Rule 8", {
                        "amount_cr": sz["amount_cr"],
                        "amount_raw": f"{sz['pct']:g}% x mcap ~Rs {sz['mcap_cr']:.0f}cr"})
                    continue
                r2["amount_cr"] = sz["amount_cr"]
                r2["amount_raw"] = (f"{sz['pct']:g}% x mcap ~Rs {sz['mcap_cr']:.0f}cr "
                                    f"= ~Rs {sz['amount_cr']:.0f}cr")
            elif src == "mcap_plausible" and sz["mcap_cr"] < config.MCAP_PLAUSIBLE_MIN:
                suppress(item, "Rule 8", {
                    "amount_cr": None,
                    "amount_raw": f"mcap ~Rs {sz['mcap_cr']:.0f}cr < {config.MCAP_PLAUSIBLE_MIN}"})
                continue

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

    funnel["alerted"] = len(alerts)
    print(f"[main] {suppressed[0]} suppressed total, {len(alerts)} alerts to send")
    if mix:
        print("[main] size_source mix: " + ", ".join(f"{k}={v}" for k, v in mix.items()))

    db.add_funnel_run(funnel)
    _report_classifier_health(fail_reasons, judged_ok[0], dry)

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
