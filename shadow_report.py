"""
Liquidity Radar — v4 Change 9: shadow-mode prefilter report.

While PREFILTER_MODE=shadow (the default), Changes 1/3/4 (pre-API amount
gate, structural blocklist, title dedup) compute their drop decision and log
it here instead of actually dropping the item — so a week of shadow
operation costs nothing extra and produces a full preview of what enforcing
them would remove.

Read this, confirm nothing real is in it, then flip the PREFILTER_MODE
repository variable to "enforce" in GitHub repo Settings -> Secrets and
variables -> Actions -> Variables.

    python shadow_report.py [--days N]
"""

import argparse
from collections import defaultdict

import db

MAX_SHOWN_PER_FILTER = 50


def _dedup_rows_as_shadow(rows):
    return [
        {
            "title": r["title"],
            "url": r["url"],
            "reason": f"matched '{r['matched_title']}' (sim={r['similarity']:.2f})",
        }
        for r in rows
    ]


def main():
    parser = argparse.ArgumentParser(description="Liquidity Radar shadow prefilter report")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    db.init_db()
    hours = args.days * 24

    by_filter = defaultdict(list)
    for r in db.prefilter_shadow_since(hours):
        by_filter[r["filter"]].append(r)
    dedup_rows = db.title_dedup_log_since(hours)
    if dedup_rows:
        by_filter["title_dedup"].extend(_dedup_rows_as_shadow(dedup_rows))

    total = sum(len(v) for v in by_filter.values())
    print(f"Shadow prefilter report — last {args.days} days ({total} would-have-dropped items)\n")

    if not by_filter:
        print("Nothing logged. Either nothing would have been dropped, or "
              "PREFILTER_MODE is already 'enforce' (structural/pre-API-amount "
              "only log in shadow mode; title_dedup always logs).")
        return

    for filt, rows in sorted(by_filter.items(), key=lambda kv: -len(kv[1])):
        print(f"\n=== {filt} ({len(rows)}) ===")
        for r in rows[:MAX_SHOWN_PER_FILTER]:
            print(f"  - {(r['title'] or '')[:96]}")
            if r.get("reason"):
                print(f"    {r['reason']}")
            if r.get("url"):
                print(f"    {r['url']}")
        if len(rows) > MAX_SHOWN_PER_FILTER:
            print(f"  … and {len(rows) - MAX_SHOWN_PER_FILTER} more")

    print(f"\n{total} total. Confirm nothing real is in here before flipping "
          f"PREFILTER_MODE to 'enforce'.")


if __name__ == "__main__":
    main()
