"""
Liquidity Radar — apply today's rules to the deals already in the database.

    python backfill_cleanup.py            # report only, changes nothing
    python backfill_cleanup.py --apply
    python backfill_cleanup.py --apply --db copy.db

Three rules shipped after most of these deals were created, so the existing
feed still shows what they were written to remove. This applies them once,
retroactively. It is a ONE-OFF: the live pipeline does all of this for new
deals, and nothing here runs on a schedule.

## What it does, and what it deliberately does not

SIZE    Deals whose amount is under THRESHOLD_CR are marked dropped. Static
        arithmetic, no API call, no judgement.

IPO     A company's listing is one story. Deals of an IPO type that share a
        clustering key and fall inside IPO_WINDOW_HOURS of the card before
        them are merged into the earliest one: their articles move across,
        holes in the survivor are filled from them, and they are marked
        dropped. This reproduces, on history, exactly what cluster._find_match
        now does live.

FUND    Needs `seller_type`, which only exists once stage 3 has read the
        article. Re-reading hundreds of old articles would be slow and would
        spend the free tier on deals the banker has already moved past, so
        --type-sellers instead classifies the distinct SELLER NAMES on their
        own, batched, with no fetching: 218 old deals share only 205 distinct
        sellers, which is a few calls rather than hundreds.

        A name alone is weaker evidence than a name plus its article, so this
        only ever ASSIGNS a type to a deal that has none, and the gate still
        refuses to drop anything with an individual named.

Nothing is deleted. A dropped deal keeps its row and gains a dropped_reason —
the audit trail stays, and the site filters on it. Every merge is reversible
from `deal_enrichment`, `deal_members` and the reason string.
"""
import argparse
import json
from collections import defaultdict
from datetime import datetime

import cluster
import config
import db
import enrich

# Fields worth carrying from a merged card onto the survivor, if the survivor
# has nothing there. Same fill-a-hole rule the rest of the pipeline uses.
CARRY = ("amount_cr", "amount_raw", "size_source", "size_band", "seller",
         "buyer", "individuals", "synopsis", "seller_type", "one_line")


def _ts(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _empty(v):
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip() or v.strip() in ("[]", "null", "UNKNOWN")
    return False


def plan_size(rows):
    """Deals already below the floor."""
    out = []
    for r in rows:
        if r.get("dropped_reason"):
            continue
        a = r.get("amount_cr")
        if a is not None and a < config.THRESHOLD_CR:
            out.append((r, f"below threshold: Rs {a:,.2f}cr < Rs {config.THRESHOLD_CR}cr"))
    return out


def plan_fund(rows):
    """Deals stage 3 has already typed as fund-owned."""
    out = []
    for r in rows:
        if r.get("dropped_reason"):
            continue
        reason = enrich.post_gate(r)
        if reason and reason.startswith("fund-owned"):
            out.append((r, reason))
    return out


def plan_ipo(rows):
    """Groups of IPO cards that are one story. Returns [(survivor, [losers])]."""
    ipo = [r for r in rows
           if cluster.is_ipo_type(r.get("deal_type")) and not r.get("dropped_reason")]

    # Group by CONTAINMENT, transitively — not by deal_key equality.
    #
    # The live matcher tests every open card with token_subset_match, so names
    # that do not match each other directly still end up on one card when a
    # third name contains both. NSE is exactly that:
    #
    #   "NSE (National Stock Exchange)"  -> {nse}
    #   "National Stock Exchange"        -> {exchange, national, stock}
    #   "National Stock Exchange (NSE)"  -> {exchange, national, nse, stock}
    #
    # The first two share no containment relation, but both sit inside the
    # third. Grouping on deal_key left NSE as two stories; union-find over
    # containment reproduces what the pipeline actually does.
    toks = {r["id"]: cluster.tokens(r.get("company") or "") for r in ipo}
    parent = {r["id"]: r["id"] for r in ipo}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for i, a_row in enumerate(ipo):
        ta = toks[a_row["id"]]
        if not ta:
            continue
        for b_row in ipo[i + 1:]:
            tb = toks[b_row["id"]]
            if tb and cluster.token_subset_match(ta, tb):
                union(a_row["id"], b_row["id"])

    by = defaultdict(list)
    for r in ipo:
        if toks[r["id"]]:
            by[find(r["id"])].append(r)

    groups = []
    for key, members in by.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda r: (r.get("created_at") or "", r["id"]))
        # Walk forward exactly as the live matcher would: a card joins the one
        # it is open against, and a gap wider than the window starts a new one.
        current, losers = members[0], []
        flushed = []
        for nxt in members[1:]:
            a, b = _ts(current.get("created_at")), _ts(nxt.get("created_at"))
            gap_h = (b - a).total_seconds() / 3600 if (a and b) else 1e9
            if gap_h <= config.IPO_WINDOW_HOURS:
                losers.append(nxt)
            else:
                if losers:
                    flushed.append((current, losers))
                current, losers = nxt, []
        if losers:
            flushed.append((current, losers))
        groups.extend(flushed)
    return groups


def apply_merge(survivor, losers, path, dry):
    """Move articles onto the survivor, fill its holes, drop the losers."""
    conn = db._conn(path)
    moved = 0
    fills = {}
    for loser in sorted(losers, key=lambda r: r.get("created_at") or ""):
        # Count BEFORE moving: after the UPDATE the loser has none left, which
        # is why the first run reported "0 articles moved" while having moved
        # 153 of them.
        moved += conn.execute("SELECT COUNT(*) FROM deal_members WHERE deal_id = ?",
                              (loser["id"],)).fetchone()[0]
        if not dry:
            conn.execute("UPDATE deal_members SET deal_id = ? WHERE deal_id = ?",
                         (survivor["id"], loser["id"]))
        for col in CARRY:
            if col in fills:
                continue
            if _empty(survivor.get(col)) and not _empty(loser.get(col)):
                fills[col] = loser[col]
    newest = max([l.get("updated_at") or l.get("created_at") or "" for l in losers]
                 + [survivor.get("updated_at") or ""])
    if not dry:
        conn.commit()
        conn.close()
        if fills:
            db.update_deal(survivor["id"], dict(fills), path=path)
        # The card must read as recent: it just absorbed later coverage.
        db.update_deal(survivor["id"], {"updated_at": newest}, path=path)
        for loser in losers:
            db.drop_deal(loser["id"],
                         f"merged into #{survivor['id']} (same IPO story)", path=path)
    else:
        conn.close()
    return moved, fills


SELLER_TYPE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "n": {"type": "INTEGER"},
            "name": {"type": "STRING"},
            "type": {"type": "STRING",
                     "enum": ["individual", "family", "fund", "company",
                              "government", "unclear"]},
        },
        "required": ["n", "name", "type"],
    },
}

SELLER_TYPE_PROMPT = """Classify each SELLER in an Indian M&A or stake-sale \
deal by what kind of party it is. You are judging the seller only.

  individual  a named person selling their own holding
  family      a promoter family, family office or family trust
  fund        a private equity, venture capital or buyout fund, a sovereign
              wealth fund, or a holding vehicle owned by one. Actis,
              Blackstone, KKR, TPG, Peak XV, Advent, Temasek, Warburg Pincus
              and their peers are funds however the name is written, including
              forms like "Blackstone arm" or "an Actis-backed vehicle".
  company     an operating business selling a subsidiary or a stake
  government  a state or central government, or a PSU divesting
  unclear     you cannot tell from the name alone

Answer "unclear" rather than guessing. A wrong "fund" hides a real lead.

Return one object per input line, in the same order."""

TYPE_BATCH = 40


def type_sellers(rows, path, dry):
    """Give old deals a seller_type from the seller NAME alone, batched.

    No article is fetched: these deals are weeks old and their value is in
    whether they should still be on the site, not in re-reading them.
    """
    import llm

    need = [r for r in rows
            if not r.get("dropped_reason")
            and (r.get("seller") or "").strip()
            and not (r.get("seller_type") or "").strip()
            and not _named(r)]
    names = sorted({(r["seller"] or "").strip() for r in need})
    if not names:
        print("  no untyped sellers")
        return {}
    print(f"  typing {len(names)} distinct seller names "
          f"({len(need)} deals), {-(-len(names) // TYPE_BATCH)} call(s)"
          f"{'  [dry run: not calling]' if dry else ''}")
    if dry:
        return {}

    verdict = {}
    for i in range(0, len(names), TYPE_BATCH):
        chunk = names[i:i + TYPE_BATCH]
        listing = "\n".join(f"{j}. {n}" for j, n in enumerate(chunk, 1))
        try:
            text = llm.complete(system=SELLER_TYPE_PROMPT, user=listing,
                                max_tokens=4096, schema=SELLER_TYPE_SCHEMA,
                                stage="seller")
            got = json.loads(text[text.find("["): text.rfind("]") + 1])
            for item, name in zip(got, chunk):
                t = (item.get("type") or "unclear").strip().lower()
                if t and t != "unclear":
                    verdict[name] = t
        except Exception as exc:  # noqa: BLE001 — a failed batch just stays untyped
            print(f"    batch {i // TYPE_BATCH + 1} failed, left untyped: "
                  f"{type(exc).__name__}: {exc}")
    for r in need:
        t = verdict.get((r["seller"] or "").strip())
        if t:
            db.update_deal(r["id"], {"seller_type": t}, path=path)
            r["seller_type"] = t
    funds = sum(1 for v in verdict.values() if v == "fund")
    print(f"    typed {len(verdict)}/{len(names)} names, {funds} of them funds")
    return verdict


def _named(r):
    try:
        return bool(json.loads(r.get("individuals") or "[]"))
    except (TypeError, ValueError):
        return False


def main():
    p = argparse.ArgumentParser(description="Apply current rules to existing deals")
    p.add_argument("--apply", action="store_true", help="write changes (default: report only)")
    p.add_argument("--db", default=db.DB_PATH)
    p.add_argument("--examples", type=int, default=10)
    p.add_argument("--type-sellers", action="store_true",
                   help="classify untyped seller NAMES first (batched, no "
                        "article fetch) so the fund gate can see old deals")
    a = p.parse_args()
    dry = not a.apply

    db.init_db(a.db)
    conn = db._conn(a.db)
    rows = [dict(r) for r in conn.execute("SELECT * FROM deals ORDER BY created_at, id")]
    conn.close()
    print(f"{len(rows)} deals in {a.db}"
          f"{'  (DRY RUN — nothing will be written)' if dry else '  — APPLYING'}\n")

    if a.type_sellers:
        print("  seller typing:")
        type_sellers(rows, a.db, dry)
        print()

    size = plan_size(rows)
    fund = plan_fund(rows)
    dropped_ids = {r["id"] for r, _ in size} | {r["id"] for r, _ in fund}
    groups = [(s, [l for l in ls if l["id"] not in dropped_ids])
              for s, ls in plan_ipo(rows)]
    groups = [(s, ls) for s, ls in groups if ls and s["id"] not in dropped_ids]

    print(f"  A. under Rs {config.THRESHOLD_CR}cr            {len(size):4} deals")
    for r, why in size[:a.examples]:
        print(f"       #{r['id']:4} Rs {r['amount_cr']:>10,.2f}cr  {(r['company'] or '')[:36]}")
    if len(size) > a.examples:
        print(f"       ... {len(size) - a.examples} more")

    print(f"\n  B. fund-owned, nobody named  {len(fund):4} deals")
    for r, why in fund[:a.examples]:
        print(f"       #{r['id']:4} {str(r['seller'])[:24]:26} {(r['company'] or '')[:30]}")

    merged_total = sum(len(ls) for _, ls in groups)
    print(f"\n  C. duplicate IPO cards       {merged_total:4} deals merged into {len(groups)} stories")
    for s, ls in sorted(groups, key=lambda g: -len(g[1]))[:a.examples]:
        print(f"       #{s['id']:4} {(s['company'] or '')[:34]:36} absorbs {len(ls)} card(s)")

    total = len(size) + len(fund) + merged_total
    print(f"\n  cards removed from the feed: {total}  ->  {len(rows) - total} remain")

    if dry:
        print("\n  re-run with --apply to write it.")
        return

    for r, why in size + fund:
        db.drop_deal(r["id"], why, path=a.db)
    moved = 0
    for s, ls in groups:
        m, _ = apply_merge(s, ls, a.db, dry=False)
        moved += m
    print(f"\n  applied. {len(size) + len(fund)} dropped, {merged_total} merged, "
          f"{moved} articles moved onto surviving cards.")


if __name__ == "__main__":
    main()
