/**
 * One feed row. The provenance underline and its always-visible caption are
 * the product's whole pitch — a reader must never have to hover or open the
 * drawer to know whether a figure is a fact or an estimate.
 */
import { useState } from "react";
import type { FeedDeal, Source } from "../data/types";
import {
  PROV_STYLE,
  amountLabel,
  peopleLabel,
  provenanceNote,
  provenanceOf,
  sourceLabel,
  timeLabel,
} from "../data/view";

export interface RowUpdate {
  time: string;
  headline: string;
}

export function FeedRow({
  deal,
  updates,
  expanded,
  onToggle,
  onOpen,
  now,
}: {
  deal: FeedDeal;
  updates: RowUpdate[];
  expanded: boolean;
  onToggle: () => void;
  onOpen: () => void;
  now: number;
}) {
  const [hover, setHover] = useState(false);
  const prov = provenanceOf(deal);
  const people = peopleLabel(deal.individuals);

  return (
    <div>
      <button
        type="button"
        className="lr-row"
        onClick={onOpen}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{ background: hover ? "var(--lr-surface)" : "transparent" }}
        aria-label={`${deal.company} — ${amountLabel(deal)}`}
      >
        <div
          className="lr-dot"
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            marginTop: 6,
            background: deal.confidence === "high" ? "var(--lr-accent)" : "transparent",
            border: deal.confidence === "high" ? "0" : "1px solid var(--lr-faint)",
          }}
        />

        <div className="lr-co" style={{ minWidth: 0 }}>
          <div className="lr-clamp2" style={{ fontSize: 14.5, fontWeight: 500, lineHeight: 1.32 }}>
            {deal.company}
          </div>
          {people && (
            <div
              style={{
                fontSize: 12,
                color: "var(--lr-accent-text)",
                marginTop: 2.8,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {people}
            </div>
          )}
        </div>

        <div className="lr-type">
          <span
            style={{
              display: "inline-block",
              fontSize: 11,
              padding: "1.4px 5.6px",
              borderRadius: 4,
              background: "var(--lr-chip)",
              color: "var(--lr-muted)",
              whiteSpace: "nowrap",
            }}
          >
            {deal.dealType}
          </span>
        </div>

        <div className="lr-amt">
          <div style={{ fontSize: 14.5, fontVariantNumeric: "tabular-nums", ...PROV_STYLE[prov] }}>
            {amountLabel(deal)}
          </div>
          <div
            style={{
              fontSize: 10.5,
              color: "var(--lr-faint)",
              marginTop: 3,
              lineHeight: 1.35,
              letterSpacing: "0.05em",
              textTransform: "uppercase",
            }}
          >
            {provenanceNote(deal, deal.primaryOutlet)}
          </div>
        </div>

        <div className="lr-sum">{deal.oneLine}</div>

        <div className="lr-meta">
          <div>{timeLabel(deal.createdAt, now)}</div>
          <div style={{ color: "var(--lr-faint)" }}>{sourceLabel(deal.sourceCount)}</div>
        </div>
      </button>

      {updates.length > 0 && (
        <div style={{ padding: "0 0 8.4px 30px" }}>
          <button
            type="button"
            onClick={(e) => {
              // Without this the row's own handler fires and opens the drawer.
              e.stopPropagation();
              onToggle();
            }}
            style={{
              background: "transparent",
              border: 0,
              padding: 0,
              color: "var(--lr-accent-text)",
              fontFamily: "inherit",
              fontSize: 11.5,
              cursor: "pointer",
            }}
          >
            {expanded
              ? `Hide ${updates.length} update${updates.length > 1 ? "s" : ""} on this deal`
              : `${updates.length} update${updates.length > 1 ? "s" : ""} on this deal ›`}
          </button>
          {expanded && (
            <div
              style={{
                marginTop: 5.6,
                borderLeft: "1px solid var(--lr-line)",
                paddingLeft: 11.2,
                display: "flex",
                flexDirection: "column",
                gap: 5.6,
              }}
            >
              {updates.map((u, i) => (
                <div key={i} style={{ display: "flex", gap: 8.4, fontSize: 12, color: "var(--lr-muted)" }}>
                  <span
                    style={{
                      flex: "none",
                      color: "var(--lr-faint)",
                      fontVariantNumeric: "tabular-nums",
                      width: 44,
                      // "2h 01m" is exactly the design's format but sits a
                      // hair over 44px at 12px, so it wrapped to two lines.
                      whiteSpace: "nowrap",
                    }}
                  >
                    {u.time}
                  </span>
                  <span style={{ minWidth: 0 }}>{u.headline}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Coverage that arrived after the first article, newest first. */
export function updatesFrom(sources: Source[], now: number): RowUpdate[] {
  return sources
    .slice(1)
    .filter((s) => s.title)
    .map((s) => ({
      time: s.attachedAt ? timeLabel(s.attachedAt, now) : "—",
      headline: s.title,
    }));
}
