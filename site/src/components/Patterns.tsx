/**
 * PATTERN alerts — one person quietly selling down a position across several
 * trades that individually cross no threshold.
 *
 * These are not deals and are deliberately not rendered as cells: there is no
 * article, no buyer, no headline. The unit is a person, a company, a running
 * total and a ledger. They sit in their own section, and the accent numeral
 * follows the digest's pattern so the page keeps one visual language.
 *
 * The section leads with the PERSON, not the company. A deal is about a
 * company; a pattern is about someone who is selling, and that name is the
 * callable lead.
 */
import { useEffect } from "react";
import type { PatternAlert } from "../data/types";
import { X } from "./icons";

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const amount = (cr: number) => `₹${inr.format(cr)}cr`;

const microLabel: React.CSSProperties = {
  fontSize: 10.5,
  fontWeight: 600,
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  color: "var(--lr-faint)",
};

export function PatternSection({
  alerts,
  onOpen,
}: {
  alerts: PatternAlert[];
  onOpen: (a: PatternAlert) => void;
}) {
  if (!alerts.length) return null;
  return (
    <div style={{ marginTop: 26, paddingTop: 14, borderTop: "2px solid var(--lr-line)" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
        <div
          style={{
            fontFamily: "var(--font-heading)",
            fontWeight: 800,
            fontSize: 11,
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            color: "var(--lr-text)",
          }}
        >
          Selling quietly
        </div>
        <div style={{ ...microLabel, marginLeft: "auto" }}>Sales that add up</div>
      </div>

      <div
        style={{
          marginTop: 8,
          fontSize: 13,
          lineHeight: 1.5,
          color: "var(--lr-muted)",
          textWrap: "pretty",
          maxWidth: "62ch",
        }}
      >
        One seller, several trades, adding up to a number none of them reached alone. Found by summing
        exchange disclosures over a rolling 90 days.
      </div>

      <div className="lr-digest" style={{ marginTop: 12 }}>
        {alerts.map((a) => (
          <div
            key={a.id}
            role="button"
            tabIndex={0}
            onClick={() => onOpen(a)}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onOpen(a))}
            onMouseEnter={(e) => (e.currentTarget.style.background = "var(--lr-hover)")}
            onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 14,
              minHeight: 62,
              padding: "10px 0",
              borderBottom: "1px solid var(--lr-line-soft)",
              cursor: "pointer",
            }}
          >
            <div
              style={{
                width: 26,
                flex: "none",
                fontFamily: "var(--font-heading)",
                fontWeight: 800,
                fontSize: 21,
                lineHeight: 1,
                color: "var(--lr-accent)",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {a.saleCount}
            </div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div
                style={{
                  fontSize: 13.5,
                  fontWeight: 600,
                  lineHeight: 1.3,
                  color: "var(--lr-text)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {a.person}
              </div>
              <div style={{ ...microLabel, marginTop: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {a.company} · {a.saleCount} sales
                {a.firstTrade === a.lastTrade ? " in one day" : ` over ${a.weeks}${a.weeks === 1 ? " week" : " weeks"}`}
              </div>
            </div>
            <div
              style={{
                fontFamily: "var(--font-heading)",
                fontWeight: 800,
                fontSize: 14,
                fontVariantNumeric: "tabular-nums",
                color: "var(--lr-text)",
                whiteSpace: "nowrap",
              }}
            >
              {amount(a.totalCr)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** The trade ledger behind one pattern. Same sheet chrome as a deal panel. */
export function PatternPanel({ alert, onClose }: { alert: PatternAlert; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const rule = { height: 2, background: "var(--lr-line)", margin: "26px 0" } as const;
  const doubled = alert.sales.filter((s) => s.sources.length > 1).length;

  return (
    <>
      <div
        className="lr-scrim"
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          zIndex: 40,
          background: "color-mix(in srgb, var(--color-neutral-900) 60%, transparent)",
        }}
      />
      <div className="lr-sheet" role="dialog" aria-label={`${alert.person} — pattern`}>
        <div
          style={{
            position: "sticky",
            top: 0,
            zIndex: 2,
            background: "var(--lr-bg)",
            padding: "14px 18px 16px",
            borderBottom: "2px solid var(--lr-line)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span
              style={{
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
              Pattern
            </span>
            <span style={microLabel}>
              {alert.saleCount} sales
              {alert.firstTrade === alert.lastTrade
                ? " · one day"
                : ` · ${alert.weeks}${alert.weeks === 1 ? " week" : " weeks"}`}
            </span>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              style={{
                marginLeft: "auto",
                width: 40,
                height: 40,
                display: "grid",
                placeItems: "center",
                cursor: "pointer",
                border: "1px solid var(--lr-line-soft)",
                background: "transparent",
                color: "var(--lr-muted)",
              }}
            >
              <X />
            </button>
          </div>
          <div
            style={{
              marginTop: 12,
              fontFamily: "var(--font-heading)",
              fontWeight: 800,
              fontSize: 22,
              lineHeight: 1.14,
              letterSpacing: "-0.02em",
              color: "var(--lr-text)",
              textWrap: "pretty",
            }}
          >
            {alert.person}
          </div>
          <div style={{ marginTop: 4, fontSize: 13.5, color: "var(--lr-muted)" }}>{alert.company}</div>
        </div>

        <div style={{ padding: "22px 18px 48px" }}>
          <div style={{ ...microLabel, letterSpacing: "0.12em" }}>Total sold</div>
          <div
            style={{
              marginTop: 12,
              fontFamily: "var(--font-heading)",
              fontWeight: 800,
              fontSize: 40,
              lineHeight: 1.05,
              letterSpacing: "-0.025em",
              fontVariantNumeric: "tabular-nums",
              color: "var(--lr-text)",
              textDecoration: "underline",
              textDecorationThickness: "2px",
              textUnderlineOffset: "5px",
            }}
          >
            {amount(alert.totalCr)}
          </div>
          <div
            style={{
              marginTop: 14,
              fontSize: 13,
              lineHeight: 1.5,
              color: "var(--lr-muted)",
              textWrap: "pretty",
              maxWidth: "52ch",
            }}
          >
            Summed from exchange disclosures rather than reported by anyone. Every figure below is a
            filed trade; the total is what they come to.
          </div>

          <div style={rule} />

          <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
            <div style={{ ...microLabel, letterSpacing: "0.12em" }}>The trades</div>
            <div style={{ ...microLabel, marginLeft: "auto" }}>
              {alert.firstTrade === alert.lastTrade
                ? alert.firstTrade
                : `${alert.firstTrade} → ${alert.lastTrade}`}
            </div>
          </div>

          <div style={{ marginTop: 10 }}>
            {alert.sales.map((s, i) => (
              <div
                key={i}
                style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(0,1fr) auto",
                  gap: 14,
                  alignItems: "baseline",
                  minHeight: 46,
                  padding: "11px 0",
                  borderBottom: "1px solid var(--lr-line-soft)",
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13.5, color: "var(--lr-text)", fontVariantNumeric: "tabular-nums" }}>
                    {s.date}
                  </div>
                  <div style={{ ...microLabel, marginTop: 3 }}>
                    {s.sources.join(" + ")} {s.sources.length > 1 ? "· same trade, both files" : "disclosure"}
                  </div>
                </div>
                <div
                  style={{
                    fontFamily: "var(--font-heading)",
                    fontWeight: 800,
                    fontSize: 15,
                    fontVariantNumeric: "tabular-nums",
                    whiteSpace: "nowrap",
                  }}
                >
                  {amount(s.valueCr)}
                </div>
              </div>
            ))}
          </div>

          {doubled > 0 && (
            <div
              style={{
                marginTop: 14,
                padding: "11px 13px",
                background: "var(--lr-inset)",
                borderLeft: "2px solid var(--lr-accent)",
                fontSize: 11.5,
                lineHeight: 1.55,
                color: "var(--lr-muted)",
              }}
            >
              {doubled === 1 ? "One trade was" : `${doubled} trades were`} reported in both the bulk and
              block deal files. Counted once here.
            </div>
          )}

          <div style={{ marginTop: 16, ...microLabel, lineHeight: 1.6, textTransform: "none", letterSpacing: 0, fontSize: 11.5 }}>
            Rolling 90-day window. Alerted {new Date(alert.alertedAt).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short" })}.
          </div>
        </div>
      </div>
    </>
  );
}
