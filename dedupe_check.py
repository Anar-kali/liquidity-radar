"""
Liquidity Radar — clustering audit.

Prints the deals clustered in the last N days together with the item titles
that merged into each, so you can verify clustering is working and not
over-merging (two genuinely different deals collapsed into one).

    python dedupe_check.py            # last 7 days
    python dedupe_check.py --days 14

v4 Change 4: --title-dedup shows the OTHER kind of merge instead — items
dropped before classification for being a near-duplicate headline. Unlike a
clustering merge (still visible in deal_members above), a title-dedup drop
leaves no other trace, so this is the only way to ever discover a wrong one.

    python dedupe_check.py --title-dedup
"""

import argparse

import db
from notify import format_amount


def _title_dedup_report(days):
    rows = db.title_dedup_log_since(days * 24)
    if not rows:
        print(f"No title-dedup drops in the last {days} days.")
        return
    for r in rows:
        print(f"\n● dropped: {(r['title'] or '')[:96]}")
        print(f"    matched: {r['matched_title']}  (similarity {r['similarity']:.2f})")
        if r.get("url"):
            print(f"    {r['url']}")
    print(f"\n{len(rows)} title-dedup drop(s) in the last {days} days.")


def main():
    parser = argparse.ArgumentParser(description="Liquidity Radar clustering audit")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--all", action="store_true",
                        help="show single-item deals too (default: only merged)")
    parser.add_argument("--title-dedup", action="store_true",
                        help="show title-dedup drops (v4 Change 4) instead of deal clusters")
    args = parser.parse_args()

    db.init_db()

    if args.title_dedup:
        _title_dedup_report(args.days)
        return

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
