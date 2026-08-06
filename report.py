"""
Liquidity Radar — suppression report.

Prints the last 7 days of suppressed items, grouped by rule, to the terminal.

    python report.py
    python report.py --days 30
"""

import argparse
from collections import defaultdict

import config
import db
from digest import RULE_LABELS
from notify import format_amount


def main():
    parser = argparse.ArgumentParser(description="Liquidity Radar suppression report")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    db.init_db()
    rows = db.suppressed_since(args.days * 24)

    print(f"\nSuppressed in the last {args.days} days: {len(rows)}\n")

    by_rule = defaultdict(list)
    for r in rows:
        by_rule[r["rule"]].append(r)

    for rule in sorted(by_rule):
        label = RULE_LABELS.get(rule, "other")
        items = by_rule[rule]
        print(f"{rule} ({label}) — {len(items)}")
        # Show the biggest few, priced first.
        items.sort(key=lambda r: (r["amount_cr"] is None, -(r["amount_cr"] or 0)))
        for r in items[:10]:
            amount = format_amount(r["amount_cr"])
            title = (r["title"] or "(untitled)")[:80]
            print(f"    {amount:>16}  {title}")
        if len(items) > 10:
            print(f"    ... and {len(items) - 10} more")
        print()


if __name__ == "__main__":
    main()
