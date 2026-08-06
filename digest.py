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

    return "\n".join(lines)


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
