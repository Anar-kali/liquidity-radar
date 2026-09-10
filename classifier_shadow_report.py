"""
Liquidity Radar — read the classifier shadow log.

    python classifier_shadow_report.py [--days 7] [--stage 1|2] [--examples 20]

While CLASSIFIER_SHADOW is set, every batch is judged twice: the primary
provider's verdict acts, the shadow provider's is recorded. This reads that log
back the way shadow_report.py reads the prefilter one, and answers the only
question that matters before a cutover — what would have changed?

## Read the two directions separately

They are not symmetric and averaging them hides the risk.

  SHADOW REJECTS, PRIMARY PASSED   the dangerous direction. Under the shadow
                                   provider these items are gone: no stage 2,
                                   no deal, no alert, and nothing downstream
                                   ever notices. Every one of these is a lead
                                   the banker would not have received.

  SHADOW PASSES, PRIMARY REJECTED  cheap. At stage 1 it costs one extra stage-2
                                   call. At stage 2 it is a deal the primary
                                   would have dropped — which may well be the
                                   shadow provider being BETTER, not worse.

Neither number is "accuracy". The primary is not ground truth; it is just what
is running today. The examples printed below are the evidence — read them.
"""
import argparse
from collections import Counter, defaultdict

import db

DEFAULT_DAYS = 7


def summarise(rows, stage):
    rows = [r for r in rows if r["stage"] == stage]
    if not rows:
        print(f"\nSTAGE {stage} — nothing logged")
        return
    n = len(rows)
    agreed = sum(r["agreed"] for r in rows)
    shadow_kills = [r for r in rows if r["shadow_reject"] and not r["primary_reject"]]
    shadow_saves = [r for r in rows if r["primary_reject"] and not r["shadow_reject"]]
    passed = sum(1 for r in rows if not r["primary_reject"])
    rejected = n - passed

    print(f"\n{'='*70}\nSTAGE {stage} — {n} items judged twice\n{'='*70}")
    print(f"  agreed                          {agreed}/{n}  ({100*agreed/n:.1f}%)")
    print(f"\n  shadow REJECTS, primary passed  {len(shadow_kills)}/{passed}"
          f"  ({100*len(shadow_kills)/passed if passed else 0:.2f}% of what the primary let through)")
    print(f"    ^ leads that would vanish silently under the shadow provider")
    print(f"  shadow passes, primary rejected {len(shadow_saves)}/{rejected}"
          f"  ({100*len(shadow_saves)/rejected if rejected else 0:.2f}% of what the primary rejected)")
    print(f"    ^ cheap at stage 1; at stage 2 this may be the shadow being better")

    # "We both rejected it, for different reasons" is weaker than the boolean.
    both = [r for r in rows if r["primary_reject"] and r["shadow_reject"]]
    differing = [r for r in both
                 if (r["primary_detail"] or "") != (r["shadow_detail"] or "")]
    if both:
        print(f"\n  both rejected, different reason {len(differing)}/{len(both)}"
              f"  ({100*len(differing)/len(both):.1f}%)")

    if stage == 1:
        by_rule = defaultdict(lambda: [0, 0])
        for r in rows:
            if r["primary_reject"]:
                key = r["primary_detail"] or "?"
                by_rule[key][1] += 1
                if r["shadow_reject"]:
                    by_rule[key][0] += 1
        if by_rule:
            print("\n  per-rule: did the shadow reject what this rule rejected?")
            for rule, (hit, total) in sorted(by_rule.items(), key=lambda kv: -kv[1][1]):
                if total >= 3:
                    print(f"    rule {rule:4} {hit:5d}/{total:<5d} {100*hit/total:5.1f}%")
    return shadow_kills


def show(rows, label, limit):
    if not rows:
        return
    print(f"\n  {label} (showing {min(limit, len(rows))} of {len(rows)}):")
    for r in rows[:limit]:
        detail = f"  [shadow: {r['shadow_detail']}]" if r["shadow_detail"] else ""
        print(f"    - {(r['title'] or '')[:88]}{detail}")


def main():
    p = argparse.ArgumentParser(description="Classifier shadow-mode report")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS)
    p.add_argument("--stage", type=int, choices=(1, 2))
    p.add_argument("--examples", type=int, default=20)
    args = p.parse_args()

    # Same as shadow_report.py: CREATE TABLE IF NOT EXISTS, so this works on a
    # database that predates the table instead of raising OperationalError.
    db.init_db()
    rows = db.classifier_shadow_since(args.days * 24)
    if not rows:
        print(f"No shadow rows in the last {args.days} days.")
        print("Set the CLASSIFIER_SHADOW repository variable (e.g. to \"gemini\") "
              "to start recording.")
        return

    pairs = Counter((r["primary_name"], r["shadow_name"]) for r in rows)
    for (primary, shadow), count in pairs.most_common():
        print(f"primary={primary}  shadow={shadow}  ({count} rows)")
    print(f"window: last {args.days} days")

    for stage in (1, 2):
        if args.stage and stage != args.stage:
            continue
        kills = summarise(rows, stage)
        show(kills, f"STAGE {stage}: leads the shadow provider would have killed",
             args.examples)

    print(f"\n{'='*70}")
    print("Read the killed leads above before switching CLASSIFIER_PROVIDER.")
    print("The percentage says how often they agreed; only the headlines say")
    print("whether the disagreements were the shadow being wrong or being right.")


if __name__ == "__main__":
    main()
