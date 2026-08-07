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
import pit_feed
import sales_tracker


def run(dry=False):
    db.init_db()

    a = deals_files.process_bulk_block_deals(dry=dry)
    b = pit_feed.process_pit_feed(dry=dry)
    n_pattern = sales_tracker.run(dry=dry)

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
