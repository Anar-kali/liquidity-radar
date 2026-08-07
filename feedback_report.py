"""
Liquidity Radar — weekly feedback report.

Groups the last 14 days of Telegram button feedback (Useful / Already knew /
Noise) and sends one Telegram message. Run every Monday 09:00 IST.

This ONLY reports. It never modifies prompts, thresholds, or rules
automatically — you read the report and edit config.py yourself.

    python feedback_report.py --dry
"""

import argparse
from collections import Counter

import db
import notify

DAYS = 14
VERDICT_LABELS = {"useful": "Useful", "already_knew": "Already knew", "noise": "Noise"}


def _breakdown(rows, field, label):
    counts = Counter((r.get(field) or "unknown") for r in rows)
    return f"  by {label}: " + ", ".join(f"{k}={v}" for k, v in counts.most_common())


def build_report():
    rows = db.feedback_since(DAYS)
    if not rows:
        return f"*Feedback — last {DAYS} days:* none logged yet."

    overall = Counter(r["verdict"] for r in rows)
    lines = [f"*Feedback — last {DAYS} days ({len(rows)} total)*", ""]
    for v in ("useful", "already_knew", "noise"):
        lines.append(f"{VERDICT_LABELS[v]}: {overall.get(v, 0)}")

    noise = [r for r in rows if r["verdict"] == "noise"]
    if noise:
        lines.append("")
        lines.append(f"*Noise breakdown ({len(noise)}):*")
        lines.append(_breakdown(noise, "deal_type", "deal type"))
        lines.append(_breakdown(noise, "size_band", "size band"))
        lines.append(_breakdown(noise, "source_feed", "source feed"))
        # No separate "which stage passed" field is tracked — every alerted
        # deal passed both classifier stages by definition. size_source
        # (stated / computed / mcap_plausible / band / unknown) is the
        # closer, more informative analog for this system's shape.
        lines.append(_breakdown(noise, "size_source", "size source"))

    knew = [r for r in rows if r["verdict"] == "already_knew"]
    if knew:
        lines.append("")
        lines.append(f"*Already-knew breakdown ({len(knew)}):*")
        lines.append(_breakdown(knew, "source_feed", "source feed"))

    if noise:
        lines.append("")
        lines.append("*Most recent noise-marked alerts:*")
        for r in noise[:5]:
            company = r.get("company") or "?"
            one_line = r.get("one_line") or ""
            url = r.get("deal_url") or ""
            lines.append(f"  - {company}: {one_line}")
            if url:
                lines.append(f"    {url}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Liquidity Radar feedback report")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    db.init_db()
    text = build_report()
    if args.dry:
        print(text)
    else:
        notify.send(text)


if __name__ == "__main__":
    main()
