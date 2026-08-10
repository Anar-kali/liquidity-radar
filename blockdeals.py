"""
Liquidity Radar — v3 Changes A + B entry point.

Runs once daily at 19:30 IST on weekdays (exchange deal files publish after
close, around 18:00-19:00 IST):

  1. deals_files.py  — NSE bulk/block deal files (Change A): CONFIRMED alerts
     for individual sellers clearing the threshold, plus records every
     individual-seller row for the aggregator below.
  2. pit_feed.py      — NSE PIT feed (Change B's primary source): standalone
     CONFIRMED alerts for large single disclosures, plus records every
     promoter/director/KMP disposal for the aggregator below.
  3. sales_tracker.py — recomputes the rolling 90-day sum per (person,
     company) and fires PATTERN alerts for salami-sliced sales that no single
     transaction would have caught.

    python blockdeals.py --dry
"""

import argparse

import db
import deals_files
import notify
import pit_feed
import sales_tracker


def _stage(name, fn, dry, fallback):
    """
    v4 Change 6 — failure isolation. Each of the three stages below is
    independent (none needs Anthropic classification for its core alerting —
    deals_files/pit_feed's own ambiguous-seller Haiku call already fails open
    internally), so a bug or outage in one must not prevent the other two
    from running. This is the block/bulk/PIT path's only fully
    API-independent alert path; it should degrade, never stop.
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        print(f"[blockdeals] {name} failed, continuing with the remaining "
              f"stages: {exc}")
        if not dry:
            notify.send(f"⚠️ Liquidity Radar: {name} failed this run ({exc}) — "
                        f"the other blockdeals stages still ran.")
        return fallback


def run(dry=False):
    db.init_db()

    a = _stage("deals_files (bulk/block)",
               lambda: deals_files.process_bulk_block_deals(dry=dry), dry,
               {"alerts": 0})
    b = _stage("pit_feed", lambda: pit_feed.process_pit_feed(dry=dry), dry,
               {"alerts": 0})
    n_pattern = _stage("sales_tracker", lambda: sales_tracker.run(dry=dry), dry, 0)

    print(f"[blockdeals] done — Change A: {a['alerts']} CONFIRMED, "
          f"Change B: {b['alerts']} CONFIRMED (large PIT) + {n_pattern} PATTERN")


def main():
    parser = argparse.ArgumentParser(description="Liquidity Radar — bulk/block deals + PIT")
    parser.add_argument("--dry", action="store_true",
                        help="fetch and classify for real, but print instead of alerting")
    args = parser.parse_args()
    run(dry=args.dry)


if __name__ == "__main__":
    main()
