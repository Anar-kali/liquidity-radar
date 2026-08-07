"""
Liquidity Radar — clustering audit.

Prints the deals clustered in the last N days together with the item titles
that merged into each, so you can verify clustering is working and not
over-merging (two genuinely different deals collapsed into one).

    python dedupe_check.py            # last 7 days
    python dedupe_check.py --days 14
"""

import argparse

import db
from notify import format_amount


def main():
    parser = argparse.ArgumentParser(description="Liquidity Radar clustering audit")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--all", action="store_true",
                        help="show single-item deals too (default: only merged)")
    args = parser.parse_args()

    db.init_db()
    deals = db.deals_in_window(args.days * 24)

    shown = 0
    for d in deals:
        members = db.members_for_deal(d["id"])
        if not args.all and len(members) < 2:
            continue  # only show deals that actually merged >1 item
        shown += 1
        amount = format_amount(d["amount_cr"])
        print(f"\n● {d['company']}  ·  {d['deal_type']}  ·  {amount}   "
              f"({len(members)} item{'s' if len(members) != 1 else ''})")
        for m in members:
            print(f"    - {(m['title'] or '')[:96]}")

    label = "deals" if args.all else "merged deals (>1 item)"
    print(f"\n{shown} {label} in the last {args.days} days.")
    if not args.all and shown == 0:
        print("(Run with --all to see single-item deals too.)")


if __name__ == "__main__":
    main()
