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

import config
import db
import notify

DAYS = 14
QUERY_STATS_DAYS = 7  # v4 Change 7 — "measure for a week, then decide"
VERDICT_LABELS = {"useful": "Useful", "already_knew": "Already knew", "noise": "Noise"}


def _breakdown(rows, field, label):
    counts = Counter((r.get(field) or "unknown") for r in rows)
    return f"  by {label}: " + ", ".join(f"{k}={v}" for k, v in counts.most_common())


def build_report():
    rows = db.feedback_since(DAYS)
    if not rows:
        lines = [f"*Feedback — last {DAYS} days:* none logged yet."]
        # v4 Change 7 is a separate data source (Google News query
        # attribution) from Telegram button feedback — it should still show
        # up even in a quiet week with zero button presses.
        query_section = _query_stats_section()
        if query_section:
            lines.append("")
            lines.append(query_section)
        return "\n".join(lines)

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

    query_section = _query_stats_section()
    if query_section:
        lines.append("")
        lines.append(query_section)

    return "\n".join(lines)


def _query_stats_section():
    """
    v4 Change 7 — per-query attribution: items produced, and how many deal
    clusters that query was the FIRST to surface. First-to-surface, not
    uniqueness, is the metric that matters — a query that mostly duplicates
    others can still be the one that gets there earliest, and lead time is
    the entire product. Cut queries that never arrive first, not queries
    that overlap. Measurement only, and only after at least two weeks: this
    never removes a query itself.
    """
    stats = db.query_item_stats(QUERY_STATS_DAYS)
    if not stats:
        return None
    lines = [f"*Google News query attribution (last {QUERY_STATS_DAYS} days):*"]
    for q in config.GOOGLE_NEWS_QUERIES:
        s = stats.get(q, {"produced": 0, "first_to_surface": 0})
        lines.append(f"  “{q}”: {s['produced']} items, "
                     f"{s['first_to_surface']} first-to-surface")
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
