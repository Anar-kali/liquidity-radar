/**
 * The top deal, enlarged. Same information as a cell, given room: 30px
 * company, 28px amount, and the raw extracted phrase shown inline — the
 * handoff calls that the trust move and says to keep it.
 */
import { useState } from "react";
import type { FeedDeal } from "../data/types";
import {
  amtStyle,
  amountLabel,
  peopleLabel,
  provenanceNote,
  provenanceOf,
  rawPhrase,
  sourceLabel,
  timeLabel,
} from "../data/view";
import { confidenceMark } from "./DealCell";

export function HeroCell({
  deal,
  leadLabel,
  onOpen,
  now,
}: {
  deal: FeedDeal;
  leadLabel: string;
  onOpen: () => void;
  now: number;
}) {
  const [hover, setHover] = useState(false);
  const prov = provenanceOf(deal);
  const raw = rawPhrase(deal);
  const people = peopleLabel(deal.individuals);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onOpen())}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        padding: "20px 16px 22px",
        background: hover ? "var(--lr-hover)" : "var(--lr-panel)",
        borderBottom: "2px solid var(--lr-line)",
        cursor: "pointer",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            fontFamily: "var(--font-heading)",
            fontWeight: 800,
            fontSize: 10.5,
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            padding: "4px 8px",
            background: "var(--lr-accent)",
            color: "var(--lr-on-accent)",
          }}
        >
          {leadLabel}
        </span>
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: "0.06em",
            textTransform: "uppercase",
            color: "var(--lr-chip-text)",
            padding: "4px 8px",
            background: "var(--lr-chip)",
          }}
        >
          {deal.dealType}
        </span>
        <span
          style={{
            marginLeft: "auto",
            fontSize: 11.5,
            color: "var(--lr-faint)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {timeLabel(deal.createdAt, now)}
        </span>
      </div>

      <div
        style={{
          marginTop: 16,
          fontFamily: "var(--font-heading)",
          fontWeight: 800,
          fontSize: 30,
          lineHeight: 1.12,
          letterSpacing: "-0.02em",
          color: "var(--lr-text)",
          overflow: "hidden",
          display: "-webkit-box",
          WebkitLineClamp: 3,
          WebkitBoxOrient: "vertical",
        }}
      >
        {deal.company}
      </div>

      <div style={{ marginTop: 18, display: "flex", alignItems: "flex-end", gap: 14, flexWrap: "wrap" }}>
        <div style={{ minWidth: 0 }}>
          <div style={amtStyle(prov, 28)}>{amountLabel(deal)}</div>
          <div
            style={{
              marginTop: 9,
              fontSize: 10.5,
              fontWeight: 600,
              letterSpacing: "0.09em",
              textTransform: "uppercase",
              color: "var(--lr-faint)",
            }}
          >
            {provenanceNote(deal, deal.primaryOutlet)}
          </div>
        </div>
        <div
          style={{
            marginLeft: "auto",
            display: "flex",
            alignItems: "center",
            gap: 7,
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: "0.06em",
            textTransform: "uppercase",
            color: "var(--lr-faint)",
            whiteSpace: "nowrap",
          }}
        >
          <span style={confidenceMark(deal.confidence)} />
          {deal.confidence === "high" ? "High confidence" : "Medium confidence"}
        </div>
      </div>

      {raw && (
        <div
          style={{
            marginTop: 16,
            padding: "10px 12px",
            background: "var(--lr-inset)",
            borderLeft: "2px solid var(--lr-accent)",
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            fontSize: 11,
            lineHeight: 1.45,
            color: "var(--lr-muted)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {raw}
        </div>
      )}

      <div style={{ marginTop: 16, fontSize: 14, lineHeight: 1.5, color: "var(--lr-muted)", textWrap: "pretty" }}>
        {deal.oneLine}
      </div>

      <div
        style={{
          marginTop: 16,
          paddingTop: 14,
          borderTop: "1px solid var(--lr-line-soft)",
          display: "flex",
          alignItems: "center",
          gap: 12,
        }}
      >
        <div
          style={{
            minWidth: 0,
            fontSize: 12.5,
            fontWeight: 600,
            color: "var(--lr-accent-text)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {people}
        </div>
        <div
          style={{
            marginLeft: "auto",
            display: "flex",
            alignItems: "center",
            gap: 12,
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: "0.06em",
            textTransform: "uppercase",
            color: "var(--lr-faint)",
            whiteSpace: "nowrap",
          }}
        >
          <span>{sourceLabel(deal.sourceCount)}</span>
          <span style={{ color: "var(--lr-accent-text)" }}>Open →</span>
        </div>
      </div>
    </div>
  );
}
