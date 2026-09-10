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

## Each stage has its OWN start date, set by when ITS prompt last changed

A verdict is only a usable label while the prompt that produced it is the
prompt under test. Compare a candidate running today's prompt against verdicts
written by an older one and you measure prompt drift, then report it as model
disagreement. The two prompts move independently, so they get separate floors:

    stage 1   SYSTEM_PROMPT         unchanged since 2026-08-10  -> 10,186 negs
    stage 2   STAGE2_SYSTEM_PROMPT  changed 08-21, 09-08, 09-09 ->    139 rows

Stage 2's prompt has moved three times in a month (the seller/buyer swap, the
company-name fix, then re-enabling buyer extraction), which collapses its
usable corpus from 4,026 rows to 139 — only 17 of them deals. That is too thin
to measure a false-negative rate and the code says so rather than quietly
reporting a number with no resolution.

Upstream FILTER changes do not invalidate a label. They change which items get
judged, not how a given (title, description) is judged. Only prompt changes
count here.

Re-derive these with `python evalset.py --prompt-history`, which hashes both
prompt bodies across the repo's commits via the GitHub API and prints when each
last moved. Do that whenever a prompt is touched — a stale floor here is
invisible and silently poisons every score.

## The pre-2026-08-10 era is excluded for a second, separate reason

Rows before 2026-08-10T09:45 also carry `gate IS NULL` and rule values the
current stage-1 prompt cannot produce — 'Rule 80', 'Rule 44', 'Rule 0'. That is
the old free-text "Rule 9: ..." schema from before v4 Change 2 introduced the
slim {n, neg, r} pass. So stage 1's floor is where both the schema and the
prompt settle.

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
import re
import sqlite3
from collections import Counter

import db

OUT_PATH = "data/evalset.json"

# When each stage's prompt last changed, and therefore the earliest verdict
# that is still a valid label for it. VERIFY WITH --prompt-history AFTER EVERY
# PROMPT EDIT; a stale value here poisons every score and shows no symptom.
#
#   stage 1  2026-08-10  4495e0e  v4 Part 1, slim {n, neg, r} schema
#   stage 2  2026-09-09  8e95447  "Re-enable buyer extraction"
#            (previously 15a68a4 09-08, 5fae7ac 08-21 seller/buyer swap)
CORPUS_FROM = {
    1: "2026-08-10T09:45",
    2: "2026-09-09T09:00",
}

# Below this many positives a false-negative rate has no resolution worth
# quoting — one miss in 17 is 5.9%, which says nothing about a 1% gate.
MIN_POSITIVES_FOR_A_RATE = 40

# config.py paths, for --prompt-history.
REPO = "Anar-kali/liquidity-radar"
PROMPT_NAMES = ("SYSTEM_PROMPT", "STAGE2_SYSTEM_PROMPT")
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


def prompt_history(limit=40):
    """When did each prompt last change? Hashes the prompt bodies across the
    repo's commits, newest first, and reports the boundary.

    Uses `gh api` rather than git log: the working clone is shallow (18
    commits, all from one day), so local history cannot answer this. Needs an
    authenticated gh; it is a maintenance command, not part of building the
    corpus.
    """
    import base64
    import hashlib
    import json as _json
    import subprocess

    def gh(path):
        out = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=60)
        if out.returncode:
            raise RuntimeError(out.stderr.strip()[:200])
        return _json.loads(out.stdout)

    def body(src, name):
        # The prompts are f-strings: NAME = f"""...
        m = re.search(rf'^{name}\s*=\s*[a-z]*"""(.*?)"""', src, re.S | re.M)
        return hashlib.sha1(m.group(1).encode()).hexdigest()[:8] if m else "MISSING"

    print(f"prompt history for {REPO} (newest first)\n")
    commits = gh(f"repos/{REPO}/commits?path=config.py&per_page={limit}")
    seen, rows = {}, []
    for c in commits:
        sha, date = c["sha"][:7], c["commit"]["committer"]["date"][:10]
        msg = c["commit"]["message"].split("\n")[0][:52]
        if msg.startswith(("state: radar run", "state: blockdeals")):
            continue
        try:
            blob = gh(f"repos/{REPO}/contents/config.py?ref={c['sha']}")
            src_at = base64.b64decode(blob["content"]).decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            print(f"  {sha} {date}  (unreadable: {exc})")
            continue
        rows.append((sha, date, msg, {n: body(src_at, n) for n in PROMPT_NAMES}))

    current = open("config.py", encoding="utf-8").read()
    now = {n: body(current, n) for n in PROMPT_NAMES}
    print(f"  {'commit':8} {'date':11} {'stage-1':10} {'stage-2':10} message")
    print(f"  {'WORKING':8} {'(now)':11} {now[PROMPT_NAMES[0]]:10} {now[PROMPT_NAMES[1]]:10}")
    for sha, date, msg, h in rows:
        marks = []
        for i, n in enumerate(PROMPT_NAMES, start=1):
            if h[n] != now[n] and n not in seen:
                seen[n] = (date, sha)
                marks.append(f"stage {i} differs from here back")
        print(f"  {sha:8} {date:11} {h[PROMPT_NAMES[0]]:10} {h[PROMPT_NAMES[1]]:10} {msg}"
              + (f"   <- {'; '.join(marks)}" if marks else ""))

    print("\n  CORPUS_FROM should be the first date at which each prompt still")
    print("  matches the working tree:")
    for i, n in enumerate(PROMPT_NAMES, start=1):
        if n in seen:
            date, sha = seen[n]
            newer = [r for r in rows if r[1] >= date and r[3][n] == now[n]]
            first = min((r[1] for r in newer), default="?")
            print(f"    stage {i}: changed at {sha} ({date}); current body from {first}"
                  f"   [config says {CORPUS_FROM[i][:10]}]")
        else:
            oldest = rows[-1][1] if rows else "?"
            print(f"    stage {i}: unchanged across all {len(rows)} commits examined "
                  f"(back to {oldest})   [config says {CORPUS_FROM[i][:10]}]")


def _placeholders(values):
    return ",".join("?" * len(values))


def _suppressed_urls(conn, rules, gate="model", since=None):
    """Distinct urls suppressed under `rules` at `gate`, with their rule."""
    sql = (f"SELECT url, MIN(rule) AS rule FROM suppressed "
           f"WHERE gate = ? AND created_at >= ? AND rule IN ({_placeholders(rules)}) "
           f"AND url NOT IN ({_placeholders(_BAD_URL)}) "
           f"GROUP BY url")
    rows = conn.execute(sql, (gate, since, *rules, *_BAD_URL))
    return {r["url"]: r["rule"] for r in rows}


def _deal_urls(conn, since):
    sql = (f"SELECT DISTINCT url FROM deals "
           f"WHERE created_at >= ? AND url NOT IN ({_placeholders(_BAD_URL)})")
    return {r["url"] for r in conn.execute(sql, (since, *_BAD_URL))}


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

    # Stage-1 labels only need stage 1's prompt to have been stable. Stage-2
    # labels need stage 2's, which is much more recent.
    s1_neg = _suppressed_urls(conn, STAGE1_NEGATIVE_RULES, since=CORPUS_FROM[1])
    s2_neg = _suppressed_urls(conn, ("Rule P",), since=CORPUS_FROM[2])
    post = _suppressed_urls(conn, POST_STAGE2_RULES, since=CORPUS_FROM[2])
    deals = _deal_urls(conn, since=CORPUS_FROM[2])
    # Stage 1's positives are "it reached stage 2", which is a stage-1 fact and
    # survives a stage-2 prompt change — so they use stage 1's floor.
    s1_pos_neg = _suppressed_urls(conn, ("Rule P",), since=CORPUS_FROM[1])
    s1_pos_post = _suppressed_urls(conn, POST_STAGE2_RULES, since=CORPUS_FROM[1])
    s1_pos_deals = _deal_urls(conn, since=CORPUS_FROM[1])
    text = _item_text(conn)
    conn.close()

    # Stage 2 approved everything that survived it AND everything the
    # post-model gates then killed.
    s2_pos = {u: "deal" for u in deals}
    s2_pos.update({u: r for u, r in post.items()})
    # Stage 1 passed everything stage 2 ever saw. What happened next is the
    # difference between "lost a lead" and "saved a call" — see the docstring.
    s1_outcome = {}
    for url in s1_pos_neg:
        s1_outcome[url] = "stage2_rejected"
    for url in s1_pos_post:
        s1_outcome[url] = "post_gate"
    for url in s1_pos_deals:
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
    p.add_argument("--prompt-history", action="store_true",
                   help="report when each prompt last changed, then exit "
                        "(verifies CORPUS_FROM; needs an authenticated gh)")
    args = p.parse_args()

    if args.prompt_history:
        prompt_history()
        return

    stage1, stage2, report = collect()

    print("FULL CORPUS — each stage limited to verdicts written by the prompt "
          "it still runs")
    print(f"  stage 1 since {CORPUS_FROM[1]}   stage 2 since {CORPUS_FROM[2]}")
    describe("stage 1", stage1)
    describe("stage 2", stage2)
    skipped = {k: v for k, v in report.items() if v}
    if skipped:
        print(f"\n  excluded: {dict(skipped)}")
        print("  (conflicting = one url labelled both ways across runs; "
              "no_item_text = suppressed/deal row whose url is not in items)")

    for stage, rows_ in ((1, stage1), (2, stage2)):
        pos = sum(1 for r in rows_ if r["label"] == "positive")
        if pos < MIN_POSITIVES_FOR_A_RATE:
            print(f"\n  !! STAGE {stage}: only {pos} positives survive the "
                  f"{CORPUS_FROM[stage][:10]} prompt floor.")
            print(f"     Too few to quote a false-negative rate against a 1% "
                  f"gate — one miss would read as {100/pos:.1f}%.")
            print(f"     Measure this stage with CLASSIFIER_SHADOW on live "
                  f"traffic instead, not from history.")

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
