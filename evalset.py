"""
Liquidity Radar — build the classifier eval corpus from verdicts already on disk.

    python evalset.py --stats          # counts only, writes nothing
    python evalset.py                  # stratified sample -> data/evalset.json
    python evalset.py --all            # every labelled example

## Why this needs no API calls

The pipeline has been recording its own verdicts since 2026-08-05. `suppressed`
holds every rejection with the rule that fired and a `gate` saying whether a
model or a deterministic filter made the call; `deals` holds the survivors.
Both carry the item url, so joining back to `items` recovers the exact title
and description the model was shown. That is ~14,600 labelled examples for
nothing, and it is why there is no replay step here.

## What the labels mean, and what they do not

These are HAIKU'S OPINIONS, not ground truth. A candidate scored against them
is being measured for AGREEMENT with the incumbent, not for accuracy. High
agreement is what licenses a swap; where the two disagree, neither the corpus
nor the score can say who was right — that pile has to be read by a human.
eval_classifier.py dumps it for exactly that reason.

## Reading the gates correctly

This is the part that is easy to get wrong, so it is spelled out:

  gate='pre-api'      a regex/blocklist dropped it. The model NEVER SAW IT.
                      Excluded entirely — it is not evidence about any model.

  Rule 1-7, 9, ?      stage 1 said "confirmed negative". `?` is a verdict whose
                      rule number came back unparseable (classify._normalise
                      maps that to None), not a missing verdict.

  Rule P              stage 2 ran and said qualify=false.

  Rule 8, R, S        (at gate='model') the item passed stage 1 AND stage 2
  with gate='model'   said qualify=TRUE — then a deterministic gate in main.py
                      killed it on size or substance. So these are stage-2
                      POSITIVES, not negatives. Counting them as rejections
                      would teach the eval that the model refused 108 deals it
                      actually approved.

  Rule 8 at 'pre-api' the SAME rule name, fired by the pre-API amount gate at
                      main.py:306, before any model call. Excluded. A rule
                      name alone is not enough to place a row — always read
                      `gate` with it.

Stage-1 positives are therefore "everything that reached stage 2" = Rule P plus
the post-gate rules plus deals.

## The corpus starts at 2026-08-10, and that is not arbitrary

Rows before 2026-08-10T09:45 carry `gate IS NULL` and rule values that the
current prompt cannot produce — 'Rule 80', 'Rule 44', 'Rule 38', 'Rule 0'.
Those are the old free-text "Rule 9: ..." schema, from before v4 Change 2
replaced it with the slim {n, neg, r} pass (see classify._normalise). Verdicts
from that era were produced by a DIFFERENT PROMPT, so scoring a candidate
against them would measure prompt drift and call it model disagreement.

Git cannot date this — the repo's history is squashed and a single commit
touches config.py — but the data dates it unambiguously: gate='model' rows and
well-formed rule numbers both begin at the same timestamp. CORPUS_FROM makes
the cut explicit rather than leaving it as a side effect of filtering on
`gate`, so backfilling that column later cannot silently widen the corpus.

## Stage-1 positives are not all equal, and the eval must not treat them so

"Haiku passed it at stage 1" is a weak label. 68% of what stage 1 passes is
killed by stage 2 anyway, so a candidate that rejects one of those at stage 1
is not losing a lead — it is saving a stage-2 call that was going to be wasted.
Measured on real disagreements: the items Gemini "wrongly" rejected were
HINDALCO analyst-meet notices, "Outcome of Board Meeting", "Stocks in news"
roundups. Gemini was right; Haiku was passing junk downstream.

So every stage-1 positive carries an `outcome` saying what happened to it next:

    deal            it became a deal. Rejecting this at stage 1 LOSES A LEAD.
    post_gate       stage 2 approved it, a deterministic gate then dropped it
                    on size. Rejecting early is harmless and cheaper.
    stage2_rejected stage 2 said no. Rejecting early is a SAVING.

Only the `deal` bucket carries real risk, and eval_classifier reports it
separately. Averaging the three produces a "false negative rate" that is mostly
measuring Haiku's stage-1 sloppiness, which is how the first run of this eval
reported 50% and meant nothing.

## url is a dirty key

3,738 items carry an empty url and 91 carry '-'. Joining on those fans out
combinatorially — it turns 10,283 suppressed rows into 15,843 "matches". They
are excluded, and every query is DISTINCT on url.
"""
import argparse
import json
import random
import sqlite3
from collections import Counter

import db

OUT_PATH = "data/evalset.json"
# First run under the current prompts — see the module docstring. Anything
# earlier was judged by a different stage-1 schema.
CORPUS_FROM = "2026-08-10T09:45"
SEED = 20260910          # fixed so two runs sample the same rows
DEFAULT_STAGE1 = 1000
DEFAULT_STAGE2 = 600

# Bad url sentinels — see the module docstring.
_BAD_URL = ("", "-")

STAGE1_NEGATIVE_RULES = ("Rule 1", "Rule 2", "Rule 3", "Rule 4", "Rule 5",
                         "Rule 6", "Rule 7", "Rule 9", "Rule ?")
# Fired AFTER stage 2 approved the item. gate='model' is load-bearing here:
# it is what separates these from the pre-API Rule 8.
POST_STAGE2_RULES = ("Rule 8", "Rule R", "Rule S")


def _placeholders(values):
    return ",".join("?" * len(values))


def _suppressed_urls(conn, rules, gate="model"):
    """Distinct urls suppressed under `rules` at `gate`, with their rule."""
    sql = (f"SELECT url, MIN(rule) AS rule FROM suppressed "
           f"WHERE gate = ? AND created_at >= ? AND rule IN ({_placeholders(rules)}) "
           f"AND url NOT IN ({_placeholders(_BAD_URL)}) "
           f"GROUP BY url")
    rows = conn.execute(sql, (gate, CORPUS_FROM, *rules, *_BAD_URL))
    return {r["url"]: r["rule"] for r in rows}


def _deal_urls(conn):
    sql = (f"SELECT DISTINCT url FROM deals "
           f"WHERE created_at >= ? AND url NOT IN ({_placeholders(_BAD_URL)})")
    return {r["url"] for r in conn.execute(sql, (CORPUS_FROM, *_BAD_URL))}


def _item_text(conn):
    """url -> (title, description).

    An url can appear in `items` more than once (re-fetches, and Google News
    reissuing the same story). The row with the longest description is the
    most informative and, being deterministic, keeps the corpus stable across
    rebuilds.
    """
    out = {}
    sql = (f"SELECT url, title, description FROM items "
           f"WHERE url NOT IN ({_placeholders(_BAD_URL)}) "
           f"ORDER BY LENGTH(COALESCE(description, '')) ASC")
    for r in conn.execute(sql, _BAD_URL):
        out[r["url"]] = (r["title"] or "", r["description"] or "")
    return out


def collect(path=db.DB_PATH):
    """Every labelled example, per stage. Returns (stage1, stage2, report)."""
    conn = db._conn(path)
    conn.row_factory = sqlite3.Row

    s1_neg = _suppressed_urls(conn, STAGE1_NEGATIVE_RULES)
    s2_neg = _suppressed_urls(conn, ("Rule P",))
    post = _suppressed_urls(conn, POST_STAGE2_RULES)
    deals = _deal_urls(conn)
    text = _item_text(conn)
    conn.close()

    # Stage 2 approved everything that survived it AND everything the
    # post-model gates then killed.
    s2_pos = {u: "deal" for u in deals}
    s2_pos.update({u: r for u, r in post.items()})
    # Stage 1 passed everything stage 2 ever saw. What happened next is the
    # difference between "lost a lead" and "saved a call" — see the docstring.
    s1_outcome = {}
    for url in s2_neg:
        s1_outcome[url] = "stage2_rejected"
    for url in post:
        s1_outcome[url] = "post_gate"
    for url in deals:
        s1_outcome[url] = "deal"          # last, so a deal always wins
    s1_pos = set(s1_outcome)

    report = Counter()

    def rows(positives, negatives, stage):
        out, conflicts = [], 0
        overlap = set(positives) & set(negatives)
        for url in sorted(set(positives) | set(negatives)):
            if url in overlap:
                # The same url labelled both ways across different runs. Real,
                # and unusable as a label — drop it rather than pick a side.
                conflicts += 1
                continue
            if url not in text:
                report[f"stage{stage}_no_item_text"] += 1
                continue
            title, desc = text[url]
            if not title.strip():
                report[f"stage{stage}_no_title"] += 1
                continue
            positive = url in positives
            row = {
                "url": url,
                "title": title,
                "description": desc,
                "label": "positive" if positive else "negative",
                "rule": (positives.get(url) if isinstance(positives, dict) and positive
                         else negatives.get(url) if not positive else None),
            }
            if positive and stage == 1:
                row["outcome"] = s1_outcome.get(url)
            out.append(row)
        report[f"stage{stage}_conflicting"] = conflicts
        return out

    stage1 = rows({u: None for u in s1_pos}, s1_neg, 1)
    stage2 = rows(s2_pos, s2_neg, 2)
    return stage1, stage2, report


def sample(rows, n, seed=SEED):
    """Stratified by label, so the mix survives sampling.

    Rows whose outcome is "deal" are ALWAYS kept, never sampled away. They are
    the only bucket where a candidate's rejection costs a real lead, there are
    only a few hundred of them, and a proportional sample would leave ~30 — too
    thin a base for the one number that decides the migration.
    """
    if not n or n >= len(rows):
        return rows
    rng = random.Random(seed)
    deals = [r for r in rows if r.get("outcome") == "deal"]
    rest = [r for r in rows if r.get("outcome") != "deal"]
    remaining = max(0, n - len(deals))
    pos = [r for r in rest if r["label"] == "positive"]
    neg = [r for r in rest if r["label"] == "negative"]
    share = len(pos) / len(rest) if rest else 0
    want_pos = min(len(pos), round(remaining * share))
    want_neg = min(len(neg), remaining - want_pos)
    picked = deals + rng.sample(pos, want_pos) + rng.sample(neg, want_neg)
    rng.shuffle(picked)
    return picked


def describe(name, rows):
    counts = Counter(r["label"] for r in rows)
    total = len(rows)
    pos = counts["positive"]
    print(f"  {name:8} {total:6d}   positive {pos:5d} ({100*pos/total if total else 0:4.1f}%)"
          f"   negative {counts['negative']:5d}")
    outcomes = Counter(r["outcome"] for r in rows if r.get("outcome"))
    if outcomes:
        parts = "  ".join(f"{k}:{v}" for k, v in outcomes.most_common())
        print(f"           positives by outcome -> {parts}")
    by_rule = Counter(r["rule"] for r in rows if r["label"] == "negative")
    if by_rule:
        top = "  ".join(f"{k}:{v}" for k, v in by_rule.most_common(6))
        print(f"           negatives by rule -> {top}")


def main():
    p = argparse.ArgumentParser(description="Build the classifier eval corpus")
    p.add_argument("--stats", action="store_true", help="report counts, write nothing")
    p.add_argument("--all", action="store_true", help="keep every example, no sampling")
    p.add_argument("--stage1", type=int, default=DEFAULT_STAGE1)
    p.add_argument("--stage2", type=int, default=DEFAULT_STAGE2)
    p.add_argument("--out", default=OUT_PATH)
    args = p.parse_args()

    stage1, stage2, report = collect()

    print(f"FULL CORPUS — verdicts recorded since {CORPUS_FROM} "
          f"(when the current prompts went live)")
    describe("stage 1", stage1)
    describe("stage 2", stage2)
    skipped = {k: v for k, v in report.items() if v}
    if skipped:
        print(f"\n  excluded: {dict(skipped)}")
        print("  (conflicting = one url labelled both ways across runs; "
              "no_item_text = suppressed/deal row whose url is not in items)")

    if args.stats:
        return

    if not args.all:
        stage1 = sample(stage1, args.stage1)
        stage2 = sample(stage2, args.stage2)
        print(f"\nSAMPLED (seed {SEED}, stratified by label)")
        describe("stage 1", stage1)
        describe("stage 2", stage2)

    payload = {
        "seed": SEED,
        "sampled": not args.all,
        "note": ("Labels are Haiku's own recorded verdicts, not ground truth. "
                 "Scores against this corpus measure agreement with the "
                 "incumbent classifier, not accuracy."),
        "counts": {"stage1": len(stage1), "stage2": len(stage2)},
        "stage1": stage1,
        "stage2": stage2,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"\nwrote {args.out}  ({len(stage1)} stage-1 + {len(stage2)} stage-2)")


if __name__ == "__main__":
    main()
