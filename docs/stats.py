"""
Regenerate every measured figure quoted in data-contract.md / design-brief.md.

    python docs/stats.py [path-to-db]        # default: radar.db

Those documents brief the site design against real distributions. When the
figures were hand-counted, three of them were wrong in the first committed
draft — a stray LIMIT truncated a distribution's tail and hid the fact that
18% of deals have no source links at all. Nothing here is typed by hand any
more: re-run this and paste, or diff it against what the docs claim.
"""
import sqlite3
import sys
from collections import Counter

DB = sys.argv[1] if len(sys.argv) > 1 else "radar.db"


def pct(n, d):
    return f"{round(100 * n / d)}%" if d else "—"


def main():
    c = sqlite3.connect(DB)
    total = c.execute("select count(*) from deals").fetchone()[0]
    members = c.execute("select count(*) from deal_members").fetchone()[0]
    lo, hi = c.execute("select min(created_at), max(created_at) from deals").fetchone()

    print(f"# Measured from {DB}\n")
    print(f"deals: {total} | source links: {members}")
    print(f"range: {lo[:10]} -> {hi[:10]}\n")

    # ---- populated-field rates -------------------------------------------
    print("## Field population\n")
    print("| Field | Populated |")
    print("|---|---|")
    for col in ("company", "deal_type", "one_line", "confidence",
                "amount_cr", "seller", "buyer"):
        n = c.execute(
            f"select count(*) from deals "
            f"where {col} is not null and {col} != ''"
        ).fetchone()[0]
        print(f"| `{col}` | {n} ({pct(n, total)}) |")
    n = c.execute(
        "select count(*) from deals "
        "where individuals is not null and individuals not in ('', '[]')"
    ).fetchone()[0]
    print(f"| `individuals` | {n} ({pct(n, total)}) |")
    # `confirmed` is an int flag, not a nullable text column: 0 means "not
    # exchange-verified", which is absence, not presence.
    n = c.execute("select count(*) from deals where confirmed = 1").fetchone()[0]
    print(f"| `confirmed` | {n} ({pct(n, total)}) |")
    withsrc = c.execute("select count(distinct deal_id) from deal_members").fetchone()[0]
    print(f"| `sources` | {withsrc} ({pct(withsrc, total)}) "
          f"— **{total - withsrc} deals have none** |")

    # ---- size provenance --------------------------------------------------
    print("\n## size_source (and whether an amount comes with it)\n")
    print("| size_source | rows | with amount |")
    print("|---|---|---|")
    for src, n, has in c.execute(
        "select coalesce(size_source,'NULL'), count(*), sum(amount_cr is not null) "
        "from deals group by 1 order by 2 desc"
    ):
        print(f"| `{src}` | {n} ({pct(n, total)}) | {has} |")

    # ---- deal types -------------------------------------------------------
    print("\n## deal_type\n")
    print("| value | rows |")
    print("|---|---|")
    for dt, n in c.execute(
        "select deal_type, count(*) from deals group by 1 order by 2 desc"
    ):
        print(f"| `{dt}` | {n} |")

    # ---- sources per deal: the distribution that was wrong ----------------
    print("\n## Sources per deal\n")
    counts = dict(c.execute(
        "select cnt, count(*) from "
        "(select deal_id, count(*) cnt from deal_members group by 1) group by 1"
    ).fetchall())
    counts[0] = total - withsrc
    print("| sources | deals |")
    print("|---|---|")
    for lo_, hi_ in [(0, 0), (1, 1), (2, 2), (3, 3), (4, 5), (6, 10),
                     (11, 20), (21, 10_000)]:
        n = sum(v for k, v in counts.items() if lo_ <= k <= hi_)
        label = str(lo_) if lo_ == hi_ else f"{lo_}–{hi_ if hi_ < 10_000 else max(counts)}"
        print(f"| {label} | {n} ({pct(n, total)}) |")
    print(f"\nmax on one deal: **{max(counts)}** | "
          f"deals above 12: {sum(v for k, v in counts.items() if k > 12)}")

    # ---- link resolvability ----------------------------------------------
    opaque = c.execute(
        "select count(*) from deal_members where url like '%news.google.com%'"
    ).fetchone()[0]
    print(f"\n## Link quality\n\nopaque Google News redirects: "
          f"{opaque}/{members} ({pct(opaque, members)}) of source links")

    # ---- company-name hygiene --------------------------------------------
    print("\n## Company-name hygiene\n")
    names = [r[0] or "" for r in c.execute("select company from deals")]
    lens = [len(n) for n in names]
    print(f"longest: {max(lens)} chars | over 40: "
          f"{sum(1 for x in lens if x > 40)} | over 70: {sum(1 for x in lens if x > 70)}")
    try:
        sys.path.insert(0, ".")
        import types
        stub = types.ModuleType("anthropic")
        stub.Anthropic = object
        sys.modules.setdefault("anthropic", stub)
        import classify

        notes = Counter()
        for n in names:
            _, note = classify.clean_company(n)
            if note:
                for part in note.split("; "):
                    notes[part] += 1
        print("\nclean_company() findings across all rows:")
        for note, n in notes.most_common():
            print(f"  {n:4d}  {note}")
        if not notes:
            print("  none — all company values clean")
    except Exception as exc:  # noqa: BLE001
        print(f"(clean_company check skipped: {exc})")


if __name__ == "__main__":
    main()
