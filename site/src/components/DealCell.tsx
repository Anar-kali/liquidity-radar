/**
 * One cell of the modular grid.
 *
 * The company name outranks the amount — 20px/600 against the amount's
 * 20px/800. The figure matters, but the name is what a banker scans for, and
 * the handoff calls this out as load-bearing rather than stylistic.
 */
import { useLayoutEffect, useRef, useState } from "react";
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
  exiting = false,
}: {
  deal: FeedDeal;
  updates: CellUpdate[];
  expanded: boolean;
  onToggle: () => void;
  onOpen: () => void;
  now: number;
  verdict?: Verdict | null;
  exiting?: boolean;
}) {
  const [hover, setHover] = useState(false);
  const [pressed, setPressed] = useState(false);

  /* Fold this cell away once it has been passed over.
     Two things make this work, and both were learned the hard way:

     The CHILD animates, not the cell. .lr-cell is a grid item, so its height
     comes from the grid row (measured: row 207.703px, item stretched to fit)
     and setting height on it is simply ignored. The child is an ordinary
     block and animates freely; .lr-exiting clips the overflow and spans the
     cell across every column so the row can follow the content down.

     It is driven with element.animate() rather than a CSS transition or
     keyframe. A transition needs a start state painted on a previous render,
     and a keyframe restarts from 0% whenever a re-render reapplies the class;
     an imperative animation starts once from a measured height and owns the
     element until it finishes. */
  const box = useRef<HTMLDivElement | null>(null);
  useLayoutEffect(() => {
    const inner = box.current?.firstElementChild as HTMLElement | undefined;
    if (!exiting || !inner) return;
    const from = inner.getBoundingClientRect().height;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const anim = inner.animate(
      [
        { height: `${from}px`, opacity: 1 },
        { height: "0px", opacity: 0 },
      ],
      { duration: reduce ? 1 : 240, easing: "cubic-bezier(0.4, 0, 0.2, 1)", fill: "forwards" },
    );
    return () => anim.cancel();
  }, [exiting]);

  const prov = provenanceOf(deal);
  const people = peopleLabel(deal.individuals);

  return (
    <div
      ref={box}
      className={"lr-cell" + (exiting ? " lr-exiting" : "")}
      aria-hidden={exiting || undefined}
    >
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
