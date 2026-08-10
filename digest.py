"""
Liquidity Radar — daily suppression digest.

Sends one Telegram message at 20:30 IST summarising what was filtered out in
the last 24 hours:

    Suppressed today: 34

    Rule 3 (govt/PSU): 6
    Rule 8 (under 250cr): 11
    Rule 9 (not a transaction): 14
    Rule 5 (intra-group): 3

    Largest suppressed: Coal India OFS Rs 5,549cr (rule 3)

Run with --dry to print instead of send.
"""

import argparse
from collections import Counter

import config
import db
import notify

RULE_LABELS = {
    "Rule 1": "pure debt",
    "Rule 2": "IBC/NCLT",
    "Rule 3": "govt/PSU",
    "Rule 4": "subsidiary sale",
    "Rule 5": "intra-group",
    "Rule 6": "no Indian individual",
    "Rule 7": "seed/Series A primary",
    "Rule 8": f"under {config.THRESHOLD_CR}cr",
    "Rule 9": "not a transaction",
    "Rule P": "failed precision check",
    "Rule S": "size band under 100cr",
    "Rule M": f"BSE company under {config.BSE_MCAP_MIN_CR}cr mcap",
    "Rule ?": "unclassified",
}


def build_digest(hours=24):
    rows = db.suppressed_since(hours)
    total = len(rows)

    lines = [f"*Suppressed today: {total}*", ""]

    if total == 0:
        lines.append("Nothing suppressed in the last 24 hours.")
        return "\n".join(lines)

    counts = Counter(r["rule"] for r in rows)
    # Show rules in numeric order, most-common tie-broken by rule number.
    for rule, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        label = RULE_LABELS.get(rule, "other")
        lines.append(f"{rule} ({label}): {n}")

    priced = [r for r in rows if r.get("amount_cr") is not None]
    if priced:
        largest = max(priced, key=lambda r: r["amount_cr"])
        amount = notify.format_amount(largest["amount_cr"])
        rule = largest["rule"].lower()
        title = largest["title"] or "(untitled)"
        lines.append("")
        lines.append(f"Largest suppressed: {title} {amount} ({rule})")

    # v3 Changes A/B: confirmed (block/bulk/PIT) and PATTERN alerts today.
    confirmed = [d for d in db.deals_in_window(hours) if d.get("confirmed")]
    patterns = db.pattern_alerts_since(hours)
    if confirmed or patterns:
        lines.append("")
        lines.append(f"Confirmed (block/bulk/PIT): {len(confirmed)} · "
                      f"Pattern (aggregated): {len(patterns)}")

    # v4 Part 1: funnel — where the volume went, across every main.py run
    # today. See SPEC-v4-upgrade.md "Before you finish" #3.
    funnel_section = _funnel_section(hours)
    if funnel_section:
        lines.append("")
        lines.append(funnel_section)

    return "\n".join(lines)


def _funnel_section(hours=24):
    runs = db.funnel_since(hours)
    if not runs:
        return None
    tot = lambda k: sum(r.get(k) or 0 for r in runs)  # noqa: E731
    return (
        f"*Funnel today ({len(runs)} runs):* fetched {tot('fetched')} → "
        f"already-seen -{tot('already_seen')} → structural -{tot('structural_dropped')} "
        f"→ title dedup -{tot('title_deduped')} → BSE mcap -{tot('bse_mcap_gated')} "
        f"→ pre-API gate -{tot('pre_api_gated')} "
        f"→ stage 1 ({tot('reached_stage1')}) → stage 2 ({tot('reached_stage2')}) "
        f"→ alerted {tot('alerted')}"
    )


def main():
    parser = argparse.ArgumentParser(description="Liquidity Radar daily digest")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    db.init_db()
    text = build_digest()
    if args.dry:
        print(text)
    else:
        notify.send(text)


if __name__ == "__main__":
    main()
