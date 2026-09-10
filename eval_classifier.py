"""
Liquidity Radar — score a classifier provider against Haiku's recorded verdicts.

    python evalset.py                                   # build the corpus first
    python eval_classifier.py --provider anthropic      # validates the harness
    python eval_classifier.py --provider gemini
    python eval_classifier.py --provider gemini --model gemini-2.5-flash-lite

## Run the incumbent first

`--provider anthropic` re-runs the model that WROTE the labels. It should agree
with itself at very close to 100%. If it does not, the gap is a bug in this
harness or in the corpus — a prompt that has changed since the verdicts were
recorded, a mis-assigned gate, a batching difference — and every score for
every other provider is meaningless until it is explained. Do not skip it, and
do not interpret a candidate's number before it passes.

The residual will not be exactly 100%: batches are formed from the corpus in
corpus order, not the order the items originally ran in, and stage 1 judges an
item alongside whatever else shares its batch. That is a real effect and it is
the floor on how precise any of this can be.

## The number that decides the migration

FALSE NEGATIVES, on stage 1 especially. A stage-1 false negative is an item the
incumbent passed and the candidate kills: it never reaches stage 2, never
becomes a deal, never alerts, and nothing downstream will ever notice. Silent
and unrecoverable. False positives cost a stage-2 call — about a thousandth of
a cent.

So the two rates are NOT symmetric and must never be summarised into a single
"accuracy" figure.

## Agreement is not accuracy

The labels are Haiku's opinions. Where a candidate disagrees, this tool cannot
say who was right — it writes the disagreements to a file so a human can. A
candidate that is genuinely better than the incumbent will score as
"disagreeing", which is why the dump matters more than the percentage.
"""
import argparse
import json
import sys
import time
from collections import defaultdict

import classify
import config

CORPUS = "data/evalset.json"
DUMP = "eval_disagreements.json"


def _configure(provider, model, stage2_model):
    """Point the seam at one provider, with NO fallback.

    Fallback is disabled deliberately: a run that quietly completes on
    Anthropic because Gemini rate-limited would report Anthropic's agreement
    under Gemini's name, which is the most misleading result this tool could
    produce.
    """
    config.CLASSIFIER_PROVIDER = provider
    config.CLASSIFIER_FALLBACK = "none"
    if model:
        config.PROVIDER_MODELS[provider][1] = model
    if stage2_model:
        config.PROVIDER_MODELS[provider][2] = stage2_model
    return (config.PROVIDER_MODELS[provider][1],
            config.PROVIDER_MODELS[provider][2])


def run_stage1(rows):
    """Returns (scored, failed) where scored is (row, predicted_negative, rule)."""
    items = [{"title": r["title"], "description": r["description"]} for r in rows]
    out = classify.classify_all(items)
    scored, failed = [], 0
    for row, (_, result) in zip(rows, out):
        if result.get("classify_failed"):
            failed += 1
            continue
        scored.append((row, result["confirmed_negative"], result.get("rule_number")))
    return scored, failed


def run_stage2(rows):
    items = [{"title": r["title"], "description": r["description"]} for r in rows]
    out = classify.precision_classify(items)
    scored, failed = [], 0
    for row, (_, result) in zip(rows, out):
        if result.get("classify_failed"):
            failed += 1
            continue
        scored.append((row, not result["qualify"], result.get("drop_reason")))
    return scored, failed


def score(stage, scored, failed, elapsed):
    """`predicted_negative` is the model's rejection; label 'negative' means
    the incumbent also rejected it."""
    n = len(scored)
    print(f"\n{'='*70}\nSTAGE {stage} — {n} scored, {failed} in failed batches, "
          f"{elapsed:.0f}s\n{'='*70}")
    if not n:
        print("  nothing scored")
        return None

    agree = fn = fp = 0
    fn_rows, fp_rows = [], []
    rule_hits = defaultdict(lambda: [0, 0])   # rule -> [agreed, total]
    for row, predicted_negative, detail in scored:
        incumbent_negative = row["label"] == "negative"
        if predicted_negative == incumbent_negative:
            agree += 1
        elif predicted_negative:
            # incumbent passed it, candidate kills it — the dangerous direction
            fn += 1
            fn_rows.append({**row, "candidate_rule": detail})
        else:
            fp += 1
            fp_rows.append({**row, "incumbent_rule": row["rule"]})
        if incumbent_negative and row["rule"]:
            rule_hits[row["rule"]][1] += 1
            if predicted_negative:
                rule_hits[row["rule"]][0] += 1

    positives = sum(1 for r, _, _ in scored if r["label"] == "positive")
    negatives = n - positives
    print(f"  agreement with incumbent      {agree}/{n}  ({100*agree/n:.1f}%)")
    print(f"\n  FALSE NEGATIVES               {fn}/{positives}  "
          f"({100*fn/positives if positives else 0:.2f}% of what the incumbent passed)")
    print(f"    ^ items the candidate kills that Haiku let through — silent, "
          f"unrecoverable")
    print(f"  false positives               {fp}/{negatives}  "
          f"({100*fp/negatives if negatives else 0:.2f}% of what the incumbent rejected)")
    print(f"    ^ costs one extra downstream call each; not a correctness problem")

    if rule_hits:
        print("\n  per-rule recall (did the candidate reject what this rule rejected?)")
        for rule, (hit, total) in sorted(rule_hits.items(), key=lambda kv: -kv[1][1]):
            if total >= 3:
                print(f"    {rule:9} {hit:5d}/{total:<5d} {100*hit/total:5.1f}%")

    return {"stage": stage, "scored": n, "failed_batches": failed,
            "agreement": round(100*agree/n, 2),
            "false_negatives": fn, "false_negative_pct": round(100*fn/positives, 3) if positives else None,
            "false_positives": fp,
            "_fn_rows": fn_rows, "_fp_rows": fp_rows}


def main():
    p = argparse.ArgumentParser(description="Score a provider against recorded verdicts")
    p.add_argument("--provider", default="gemini", choices=sorted(config.PROVIDER_MODELS))
    p.add_argument("--model", help="override the stage-1 model")
    p.add_argument("--stage2-model", help="override the stage-2 model")
    p.add_argument("--corpus", default=CORPUS)
    p.add_argument("--limit", type=int, help="score only the first N of each stage")
    p.add_argument("--stage", type=int, choices=(1, 2), help="one stage only")
    p.add_argument("--dump", default=DUMP)
    args = p.parse_args()

    try:
        corpus = json.load(open(args.corpus, encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"{args.corpus} not found — run `python evalset.py` first")

    m1, m2 = _configure(args.provider, args.model, args.stage2_model)
    s1 = corpus["stage1"][: args.limit] if args.limit else corpus["stage1"]
    s2 = corpus["stage2"][: args.limit] if args.limit else corpus["stage2"]

    print(f"provider {args.provider}   stage1={m1}   stage2={m2}")
    print(f"corpus   {args.corpus}   batch={config.BATCH_SIZE}   fallback disabled")
    print(f"calls    ~{-(-len(s1)//config.BATCH_SIZE) + -(-len(s2)//config.BATCH_SIZE)}")
    print(corpus.get("note", ""))

    results = []
    if args.stage != 2:
        t = time.time()
        scored, failed = run_stage1(s1)
        results.append(score(1, scored, failed, time.time() - t))
    if args.stage != 1:
        t = time.time()
        scored, failed = run_stage2(s2)
        results.append(score(2, scored, failed, time.time() - t))

    results = [r for r in results if r]
    dump = {"provider": args.provider, "stage1_model": m1, "stage2_model": m2,
            "stages": []}
    for r in results:
        fn_rows, fp_rows = r.pop("_fn_rows"), r.pop("_fp_rows")
        dump["stages"].append({**r, "false_negatives_detail": fn_rows,
                               "false_positives_detail": fp_rows})
    with open(args.dump, "w", encoding="utf-8") as fh:
        json.dump(dump, fh, ensure_ascii=False, indent=1)

    print(f"\n{'='*70}")
    print(f"wrote {args.dump} — READ THE FALSE NEGATIVES. The percentage says")
    print("how often the candidate agreed; only the rows say who was right.")
    worst = max((r["false_negative_pct"] or 0) for r in results) if results else 0
    if args.provider == "anthropic":
        low = min(r["agreement"] for r in results)
        print(f"\nharness check: incumbent agreed with itself {low:.1f}% "
              f"({'OK' if low >= 95 else 'TOO LOW — fix the harness before trusting any candidate'})")
    elif worst > 1.0:
        print(f"\nGATE: stage false-negative rate {worst:.2f}% exceeds the 1% "
              f"ceiling set in the plan. Read the dump before going further.")


if __name__ == "__main__":
    main()
