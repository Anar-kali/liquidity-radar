/**
 * One cell of the modular grid.
 *
 * The company name outranks the amount — 20px/600 against the amount's
 * 20px/800. The figure matters, but the name is what a banker scans for, and
 * the handoff calls this out as load-bearing rather than stylistic.
 */
import { useState } from "react";
import type { FeedDeal } from "../data/types";
import type { Verdict } from "../data/review";
import { amountLabel, amtStyle, peopleLabel, provenanceNote, provenanceOf, recencyDate, sourceLabel, timeLabel } from "../data/view";

export interface CellUpdate {
  time: string;
  headline: string;
}

const label: React.CSSProperties = {
  fontSize: 10.5,
  fontWeight: 600,
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  color: "var(--lr-faint)",
};

export function confidenceMark(confidence: string): React.CSSProperties {
  return {
    width: 9,
    height: 9,
    flex: "none",
    ...(confidence === "high"
      ? { background: "var(--lr-accent)" }
      : { border: "1.5px solid var(--lr-faint)" }),
  };
}

export function DealCell({
  deal,
  updates,
  expanded,
  onToggle,
  onOpen,
  now,
  verdict = null,
}: {
  deal: FeedDeal;
  updates: CellUpdate[];
  expanded: boolean;
  onToggle: () => void;
  onOpen: () => void;
  now: number;
  verdict?: Verdict | null;
}) {
  const [hover, setHover] = useState(false);
  const [pressed, setPressed] = useState(false);

  /* Both shapes of this cell carry the same view-transition-name, which is
     what lets the browser tween one into the other when a verdict lands:
     the card squashes into the bar while its neighbours slide into the row
     it gave up. Without the name the change is a cut. */
  const morph = { viewTransitionName: `lr-deal-${deal.id}` } as React.CSSProperties;

  /* Passed: a thin full-width bar, left exactly where the deal already was.
     .lr-passed spans every column so it owns its row and can be as short as
     its contents — the earlier version shrank a cell inside a shared row,
     which left a full-card hole beside it. Staying in place also means the
     deal never appears to vanish. */
  if (verdict === "reject") {
    return (
      <div className="lr-cell lr-passed" style={morph}>
        <div
          role="button"
          tabIndex={0}
          className="lr-passed-row"
          onClick={onOpen}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onOpen())}
          title="Passed over — open it to change your mind"
        >
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: "0.09em",
              textTransform: "uppercase",
              flex: "none",
            }}
          >
            Passed
          </span>
          <span
            style={{
              fontSize: 13,
              flex: 1,
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {deal.company}
          </span>
          <span style={{ fontSize: 12, fontVariantNumeric: "tabular-nums", flex: "none" }}>
            {amountLabel(deal)}
          </span>
        </div>
      </div>
    );
  }

  const prov = provenanceOf(deal);
  const people = peopleLabel(deal.individuals);

  return (
    <div className="lr-cell" style={morph}>
      <div
        role="button"
        tabIndex={0}
        onClick={onOpen}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onOpen())}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => {
          setHover(false);
          setPressed(false);
        }}
        onMouseDown={() => setPressed(true)}
        onMouseUp={() => setPressed(false)}
        onTouchStart={() => setPressed(true)}
        onTouchEnd={() => setPressed(false)}
        style={{
          padding: "17px 16px",
          cursor: "pointer",
          // Shortlisted: an accent rail and an inset ground. The system marks
          // emphasis with alignment and divider strength, not colour washes,
          // so this borrows the vocabulary already in use rather than adding
          // a badge or a new hue.
          borderLeft: verdict === "shortlist" ? "3px solid var(--lr-accent)" : "3px solid transparent",
          background:
            pressed || hover
              ? "var(--lr-hover)"
              : verdict === "shortlist"
                ? "var(--lr-inset)"
                : "transparent",
          transform: pressed ? "scale(0.99)" : "scale(1)",
          transition: "background 120ms ease, transform 120ms ease",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <span
            style={{
              ...label,
              color: "var(--lr-chip-text)",
              padding: "3px 7px",
              background: "var(--lr-chip)",
              whiteSpace: "nowrap",
            }}
          >
            {deal.dealType}
          </span>
          <span style={confidenceMark(deal.confidence)} />
          <span style={label}>{deal.confidence === "high" ? "High" : "Medium"}</span>
          <span
            style={{
              marginLeft: "auto",
              fontSize: 11,
              color: "var(--lr-faint)",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {/* An IPO card gathers coverage for up to 60 days, so it shows
                when it last moved rather than when it was opened — "6w" on a
                listing that made news this morning is just wrong. Other deals
                are unchanged. */}
            {timeLabel(recencyDate(deal), now)}
          </span>
        </div>

        <div
          style={{
            marginTop: 12,
            fontFamily: "var(--font-heading)",
            fontWeight: 600,
            fontSize: 20,
            lineHeight: 1.22,
            letterSpacing: "-0.015em",
            color: "var(--lr-text)",
            overflow: "hidden",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
          }}
        >
          {deal.company}
        </div>

        <div style={{ marginTop: 12, display: "flex", alignItems: "flex-end", gap: 12 }}>
          <div style={amtStyle(prov, 20)}>{amountLabel(deal)}</div>
          <div
            title={provenanceNote(deal, deal.primaryOutlet)}
            style={{
              marginLeft: "auto",
              textAlign: "right",
              fontSize: 10,
              fontWeight: 600,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              color: "var(--lr-faint)",
              lineHeight: 1.5,
              maxWidth: "44%",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {provenanceNote(deal, deal.primaryOutlet)}
          </div>
        </div>

        <div
          style={{
            marginTop: 12,
            fontSize: 13,
            lineHeight: 1.5,
            color: "var(--lr-muted)",
            overflow: "hidden",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
          }}
        >
          {deal.oneLine}
        </div>

        <div
          style={{
            marginTop: 13,
            display: "flex",
            alignItems: "center",
            gap: 10,
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: "0.05em",
            textTransform: "uppercase",
            color: "var(--lr-faint)",
          }}
        >
          {people && (
            <span
              title={people}
              style={{
                color: "var(--lr-accent-text)",
                letterSpacing: 0,
                textTransform: "none",
                fontSize: 12,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                minWidth: 0,
              }}
            >
              {people}
            </span>
          )}
          <span style={{ marginLeft: "auto", whiteSpace: "nowrap" }}>
            {sourceLabel(deal.sourceCount)}
          </span>
        </div>
      </div>

      {updates.length > 0 && (
        <div style={{ padding: "0 16px 6px" }}>
          <button
            type="button"
            onClick={(e) => {
              // Without this the cell's own handler fires and opens the panel.
              e.stopPropagation();
              onToggle();
            }}
            style={{
              width: "100%",
              minHeight: 44,
              display: "flex",
              alignItems: "center",
              textAlign: "left",
              fontFamily: "inherit",
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              cursor: "pointer",
              border: 0,
              borderTop: "1px solid var(--lr-line-soft)",
              background: "transparent",
              color: "var(--lr-accent-text)",
            }}
          >
            {expanded
              ? `Hide ${updates.length} update${updates.length > 1 ? "s" : ""} on this deal`
              : `${updates.length} update${updates.length > 1 ? "s" : ""} on this deal →`}
          </button>
          {expanded && (
            <div
              style={{
                padding: "2px 0 14px 12px",
                borderLeft: "2px solid var(--lr-line-soft)",
                display: "flex",
                flexDirection: "column",
                gap: 10,
              }}
            >
              {updates.map((u, i) => (
                <div key={i} style={{ display: "flex", gap: 10 }}>
                  <div
                    style={{
                      width: 48,
                      flex: "none",
                      fontSize: 11,
                      color: "var(--lr-faint)",
                      fontVariantNumeric: "tabular-nums",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {u.time}
                  </div>
                  <div style={{ fontSize: 12, lineHeight: 1.45, color: "var(--lr-muted)" }}>
                    {u.headline}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
