"""
Liquidity Radar — v3 Change B: salami-slice aggregation.

A promoter sells Rs 120cr three times over six weeks. No single sale trips
the threshold and no publication writes about a series of unremarkable
promoter sales. Cumulatively it is Rs 360cr and nothing else in the system
sees it. This module does: after every insert into individual_sales (from the
PIT feed, block/bulk deal files, or news), recompute the rolling 90-day sum
per (person, company) and fire a PATTERN alert when it crosses the threshold.

This is expected to fire a handful of times a QUARTER, not weekly — the value
is that nothing else in the market catches this pattern, not that it fires
often.

Report-only in spirit, like the feedback report: this module never touches
prompts, thresholds, or the classifier. It only aggregates structured data
already sitting in individual_sales.
"""

from datetime import datetime, timedelta, timezone

import config
import db
import notify

_DATE_FORMATS = ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d")


def _log(msg):
    print(f"[sales_tracker] {msg}")


def _parse_date(s):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _span_weeks(rows):
    dates = [d for d in (_parse_date(r["trade_date"]) for r in rows) if d]
    if len(dates) < 2:
        return "several"
    span_days = (max(dates) - min(dates)).days
    return max(1, round(span_days / 7))


def _chronological(rows):
    """
    Sort by trade date. NOT reusing the DB's ORDER BY: trade_date is stored as
    whatever display string the source gave ("01-Jul-2026"), and that format
    doesn't sort correctly as text (e.g. "Jul" < "Jun" alphabetically breaks
    month order). Falls back to created_at (always ISO, always present) for
    any row whose date string doesn't parse.
    """
    def sort_key(r):
        d = _parse_date(r["trade_date"])
        return (0, d) if d else (1, r["created_at"])
    return sorted(rows, key=sort_key)


def _should_fire(rows, last_alert):
    """Apply the aggregation + re-alert rule. Returns (fire: bool, total: float)."""
    total = round(sum(r["value_cr"] for r in rows), 2)
    if total < config.AGGREGATION_MIN_CR:
        return False, total
    if len(rows) < config.AGGREGATION_MIN_TRANSACTIONS:
        return False, total

    max_single = max(r["value_cr"] for r in rows)
    if max_single > config.AGGREGATION_MAX_SINGLE_SHARE * total:
        return False, total  # one transaction already accounts for this; the
                              # normal single-transaction pipeline caught it

    if last_alert is None:
        return True, total

    if total >= config.AGGREGATION_REALERT_MULTIPLE * last_alert["total_cr"]:
        return True, total

    alerted_at = datetime.fromisoformat(last_alert["alerted_at"])
    if alerted_at.tzinfo is None:
        alerted_at = alerted_at.replace(tzinfo=timezone.utc)
    cooldown = timedelta(days=config.AGGREGATION_REALERT_COOLDOWN_DAYS)
    if datetime.now(timezone.utc) - alerted_at >= cooldown:
        return True, total

    return False, total


def run(dry=False):
    """Check every (person, company) pair with recent activity; fire PATTERN
    alerts for the ones that qualify."""
    pairs = db.distinct_person_companies(config.AGGREGATION_WINDOW_DAYS)
    fired = 0
    for person_key, company_key in pairs:
        rows = db.sales_for_person_company(person_key, company_key,
                                            config.AGGREGATION_WINDOW_DAYS)
        if not rows:
            continue
        last_alert = db.last_pattern_alert(person_key, company_key)
        fire, total = _should_fire(rows, last_alert)
        if not fire:
            continue

        ordered = _chronological(rows)
        person_name = ordered[0]["person_name"]
        company = ordered[0]["company"]
        transactions = [(r["trade_date"], r["value_cr"]) for r in ordered]
        weeks = _span_weeks(ordered)

        if dry:
            _log(f"(dry) would fire PATTERN: {person_name} / {company} / "
                 f"Rs {total:g}cr over {len(rows)} sales")
            fired += 1
            continue

        # Only record the alert as sent if it actually was — otherwise a
        # failed Telegram send (network error, or a still-too-long message)
        # would mark this pattern as "already alerted" and the 2x-or-90-day
        # cooldown would silently block ever retrying it, even though the
        # user never saw it.
        if notify.send_pattern_alert(person_name, company, total, transactions, weeks):
            db.record_pattern_alert(person_key, company_key, total)
            fired += 1
        else:
            _log(f"PATTERN alert send FAILED for {person_name} / {company} — "
                 f"will retry next run")

    _log(f"{len(pairs)} person/company pairs checked, {fired} PATTERN alert(s) "
         f"{'would fire' if dry else 'fired'}")
    return fired
