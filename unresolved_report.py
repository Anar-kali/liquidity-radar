"""
Liquidity Radar — v5 Change 5: unresolved company names, and fuzzy matches.

Two questions, one report.

1. WHICH ALIAS SHOULD I ADD NEXT? Company names that resolve under no tier of
   the matcher are listed by how often they turn up. Most will be genuinely
   private companies — Zepto, Zetwerk, Table Space — and the right answer for
   those is to leave them alone; the market-cap gate never touches them and
   they reach you as before. What you are hunting for is a LISTED company
   hiding behind a brand name the master list doesn't use ("Nykaa" is filed as
   "FSN E-Commerce Ventures"). Add those to data/aliases.csv as
   `brand,legal_name` — the legal name, never a ticker, so a symbol change
   can't rot the file.

2. DID A FUZZY MATCH KILL SOMETHING REAL? The subset and alias tiers enforce
   from day one, and a wrong match suppresses a deal INVISIBLY — nothing
   arrives for you to notice. So every non-exact match is logged with what it
   matched, the rare word that carried it, and what the pipeline then did.
   Read the `suppressed` half of that list. If a company in it is not the
   company named in the headline, that is a bad match: raise
   config.MATCH_RARE_TOKEN_MAX_DF, or add a correcting alias.

    python unresolved_report.py [--days N]
"""

import argparse
from collections import Counter

import db
import sizing

MAX_SHOWN = 40


def main():
    parser = argparse.ArgumentParser(
        description="Liquidity Radar unresolved-name / fuzzy-match report")
    parser.add_argument("--days", type=int, default=30,
                        help="how far back to look (default 30)")
    args = parser.parse_args()

    deals = db.deals_since(args.days) if hasattr(db, "deals_since") else None
    if deals is None:
        import sqlite3
        conn = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        deals = [dict(r) for r in conn.execute(
            "SELECT company, amount_cr, created_at FROM deals "
            "WHERE created_at >= datetime('now', ?)", (f"-{args.days} days",))]
        conn.close()

    print(f"\n{'=' * 72}\nUNRESOLVED COMPANY NAMES — last {args.days} days\n{'=' * 72}")

    unresolved, resolved = Counter(), Counter()
    for d in deals:
        name = (d.get("company") or "").strip()
        if not name:
            continue
        match = sizing.resolve_company(name)
        if match:
            resolved[match["tier"]] += 1
        else:
            unresolved[name] += 1

    total = sum(resolved.values()) + sum(unresolved.values())
    if not total:
        print("\nNo deals in this window.\n")
        return

    by_tier = ", ".join(f"{t}={n}" for t, n in resolved.most_common()) or "none"
    print(f"\n{total} alerted companies: {sum(resolved.values())} resolve "
          f"({by_tier}), {sum(unresolved.values())} do not.\n")
    print("Most frequent names that resolve under NO tier.")
    print("Add ONLY the ones you know are listed under a different legal name.\n")
    for name, n in unresolved.most_common(MAX_SHOWN):
        print(f"   x{n:<3} {name[:62]}")
    if len(unresolved) > MAX_SHOWN:
        print(f"   ... and {len(unresolved) - MAX_SHOWN} more distinct names")

    print(f"\n{'=' * 72}\nNON-EXACT NAME MATCHES — last {args.days} days\n{'=' * 72}")
    rows = db.name_matches_since(args.days)
    if not rows:
        print("\nNothing logged yet. Either no fuzzy match has fired, or the\n"
              "pipeline has not run since v5 shipped.\n")
        return

    suppressed = [r for r in rows if r["decision"] == "suppressed"]
    passed = [r for r in rows if r["decision"] != "suppressed"]
    print(f"\n{len(rows)} fuzzy matches: {len(suppressed)} suppressed the item, "
          f"{len(passed)} let it through.")

    print("\n--- SUPPRESSED (read these carefully — a wrong one is a lost deal) ---")
    if not suppressed:
        print("   none")
    for r in suppressed[:MAX_SHOWN]:
        carried = (f" on '{r['rare_token']}' (in {r['rare_df']} names)"
                   if r["rare_token"] else "")
        mcap = f"{r['mcap_cr']:.0f}cr" if r["mcap_cr"] is not None else "?"
        print(f"\n   [{r['tier']}] {r['input_name'][:44]}")
        print(f"      matched -> {r['master_name'][:48]} ({r['ticker']}){carried}")
        print(f"      mcap {mcap} — item: {(r['title'] or '')[:58]}")

    print("\n--- PASSED ---")
    for r in passed[:15]:
        print(f"   [{r['tier']}] {r['input_name'][:38]} -> "
              f"{(r['master_name'] or '')[:38]} ({r['ticker']})")
    if len(passed) > 15:
        print(f"   ... and {len(passed) - 15} more")
    print()


if __name__ == "__main__":
    main()
