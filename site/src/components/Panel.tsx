/**
 * Deal detail. Full-screen on phone, right-docked 580px on desktop — the
 * .lr-sheet class handles both, so nothing here may set those properties
 * inline.
 *
 * Two rules from the handoff that look like omissions but are not:
 *  - Absence is STATED, never blank. An unnamed seller gets a sentence.
 *  - Advisers do not render at all. The field is 0% populated and an empty
 *    row would advertise the gap.
 */
import { useEffect, useState } from "react";
import type { Deal } from "../data/types";
import {
  PROV_SENTENCE,
  amtStyle,
  amountLabel,
  linkFor,
  provenanceOf,
  rawPhrase,
  timeLabel,
} from "../data/view";
import { X } from "./icons";

const GATED_PLACEHOLDER = [
  { name: "Promoter family trust", stake: "41.20%", pledged: "0.00%", qoq: "−1.4" },
  { name: "Founder (individual)", stake: "12.80%", pledged: "2.10%", qoq: "−0.6" },
  { name: "Family holding vehicle", stake: "7.35%", pledged: "0.00%", qoq: "0.0" },
  { name: "Promoter group — others", stake: "3.90%", pledged: "0.00%", qoq: "+0.2" },
];

const sectionLabel: React.CSSProperties = {
  fontSize: 10.5,
  fontWeight: 600,
  letterSpacing: "0.12em",
  textTransform: "uppercase",
  color: "var(--lr-faint)",
};

const microLabel: React.CSSProperties = {
  fontSize: 10.5,
  fontWeight: 600,
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  color: "var(--lr-faint)",
};

/** 2px full-width rule, the system's section separator. */
const rule: React.CSSProperties = { height: 2, background: "var(--lr-line)", margin: "26px 0" };

export function Panel({ deal, onClose, now }: { deal: Deal; onClose: () => void; now: number }) {
  const [allSources, setAllSources] = useState(false);
  const prov = provenanceOf(deal);
  const raw = rawPhrase(deal);

  useEffect(() => setAllSources(false), [deal.id]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const withTitles = deal.sources.filter((s) => s.title);
  const shown = allSources ? withTitles : withTitles.slice(0, 6);

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
      <div className="lr-sheet" role="dialog" aria-label={deal.company}>
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
                ...microLabel,
                color: "var(--lr-chip-text)",
                padding: "3px 7px",
                background: "var(--lr-chip)",
              }}
            >
              {deal.dealType}
            </span>
            <span style={microLabel}>
              {deal.confidence === "high" ? "High confidence" : "Medium confidence"}
              {" · "}
              {timeLabel(deal.updatedAt, now)}
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
            {deal.company}
          </div>
        </div>

        <div style={{ padding: "22px 18px 48px" }}>
          {/* ── quantum ─────────────────────────────────────────────── */}
          <div style={sectionLabel}>Quantum</div>
          <div style={{ marginTop: 12, ...amtStyle(prov, 40) }}>{amountLabel(deal)}</div>
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
            {PROV_SENTENCE[prov]}
          </div>
          {raw && (
            <div
              style={{
                marginTop: 14,
                padding: "11px 13px",
                background: "var(--lr-inset)",
                borderLeft: "2px solid var(--lr-accent)",
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                fontSize: 11.5,
                lineHeight: 1.5,
                color: "var(--lr-muted)",
                wordBreak: "break-word",
              }}
            >
              {raw}
            </div>
          )}

          <div style={rule} />

          {/* ── who gets paid ───────────────────────────────────────── */}
          <div style={sectionLabel}>Who gets paid</div>
          <div
            style={{
              marginTop: 14,
              display: "grid",
              gridTemplateColumns: "84px minmax(0,1fr)",
              gap: "12px 14px",
              alignItems: "baseline",
            }}
          >
            <div style={microLabel}>Individuals</div>
            <div>
              {deal.individuals.length ? (
                <span style={{ fontSize: 15, fontWeight: 600, color: "var(--lr-accent-text)" }}>
                  {deal.individuals.join(", ")}
                </span>
              ) : (
                <span style={{ fontSize: 13, color: "var(--lr-faint)" }}>
                  No individual named yet — the pipeline records one in 14% of deals
                </span>
              )}
            </div>

            <div style={microLabel}>Selling</div>
            <div>
              {deal.seller ? (
                <span style={{ fontSize: 14 }}>{deal.seller}</span>
              ) : (
                <span style={{ fontSize: 13, color: "var(--lr-faint)" }}>
                  Selling entity not disclosed in any source so far
                </span>
              )}
            </div>

            {deal.buyer && (
              <>
                <div style={microLabel}>Buyer</div>
                <div style={{ fontSize: 14 }}>{deal.buyer}</div>
              </>
            )}
          </div>

          <div style={rule} />

          {/* ── coverage ────────────────────────────────────────────── */}
          <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
            <div style={sectionLabel}>Coverage</div>
            <div style={{ ...microLabel, marginLeft: "auto" }}>
              {withTitles.length === 0
                ? "No press coverage"
                : `${withTitles.length} ${withTitles.length === 1 ? "article" : "articles clustered"}`}
            </div>
          </div>

          {withTitles.length === 0 && (
            <div
              style={{
                marginTop: 13,
                fontSize: 13,
                lineHeight: 1.5,
                color: "var(--lr-faint)",
                textWrap: "pretty",
              }}
            >
              Picked up from an exchange filing, not from press. No article to open.
            </div>
          )}

          <div style={{ marginTop: 8 }}>
            {shown.map((s, i) => {
              const link = linkFor(s);
              return (
                <a
                  key={i}
                  className="lr-src"
                  href={link.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{
                    display: "grid",
                    gridTemplateColumns: "minmax(0,1fr) auto",
                    gap: 14,
                    alignItems: "baseline",
                    minHeight: 56,
                    padding: "13px 0",
                    borderBottom: "1px solid var(--lr-line-soft)",
                    textDecoration: "none",
                    color: "inherit",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "var(--lr-hover)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                >
                  <div style={{ minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: 13.5,
                        lineHeight: 1.4,
                        color: "var(--lr-text)",
                        overflow: "hidden",
                        display: "-webkit-box",
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: "vertical",
                      }}
                    >
                      {s.title}
                    </div>
                    <div style={{ ...microLabel, marginTop: 4 }}>
                      {s.outlet}
                      {s.attachedAt ? ` · ${timeLabel(s.attachedAt, now)}` : ""}
                    </div>
                  </div>
                  <div
                    style={{
                      fontSize: 10.5,
                      fontWeight: 600,
                      letterSpacing: "0.06em",
                      textTransform: "uppercase",
                      whiteSpace: "nowrap",
                      color: link.accent ? "var(--lr-accent-text)" : "var(--lr-faint)",
                    }}
                  >
                    {link.label}
                  </div>
                </a>
              );
            })}
          </div>

          {withTitles.length > 6 && !allSources && (
            <button
              type="button"
              onClick={() => setAllSources(true)}
              style={{
                marginTop: 16,
                width: "100%",
                minHeight: 46,
                fontFamily: "var(--font-heading)",
                fontWeight: 600,
                fontSize: 11,
                letterSpacing: "0.08em",
                textTransform: "uppercase",
                cursor: "pointer",
                border: "1px solid var(--lr-line-soft)",
                background: "transparent",
                color: "var(--lr-text)",
              }}
            >
              Show all {withTitles.length} articles
            </button>
          )}

          <div
            style={{
              marginTop: 14,
              fontSize: 11.5,
              lineHeight: 1.6,
              color: "var(--lr-faint)",
              textWrap: "pretty",
            }}
          >
            Most links arrive as Google News redirect tokens — the outlet is reliable, the destination is an
            interstitial. Marked accordingly rather than promising a page we can't deliver.
          </div>

          <div style={rule} />

          {/* ── gated company profile ───────────────────────────────── */}
          <div style={{ border: "1px solid var(--lr-line-soft)", padding: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ ...sectionLabel, color: "var(--lr-text)", fontFamily: "var(--font-heading)", fontWeight: 800 }}>
                Company profile
              </div>
              <span
                style={{
                  marginLeft: "auto",
                  fontSize: 10,
                  fontWeight: 800,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  padding: "3px 7px",
                  background: "var(--lr-accent)",
                  color: "var(--lr-on-accent)",
                }}
              >
                Locked
              </span>
            </div>
            <div style={{ marginTop: 12, fontSize: 13.5, lineHeight: 1.5, color: "var(--lr-muted)" }}>
              Promoter shareholding, pledges and quarter-on-quarter change
            </div>
            <div
              style={{
                marginTop: 14,
                display: "grid",
                gridTemplateColumns: "minmax(0,1fr) 64px 64px 56px",
                gap: 8,
                paddingBottom: 8,
                borderBottom: "2px solid var(--lr-line)",
                ...microLabel,
              }}
            >
              <div>Holder</div>
              <div style={{ textAlign: "right" }}>Stake</div>
              <div style={{ textAlign: "right" }}>Pledged</div>
              <div style={{ textAlign: "right" }}>QoQ</div>
            </div>
            <div aria-hidden style={{ filter: "blur(4.5px)", opacity: 0.5, userSelect: "none", pointerEvents: "none" }}>
              {GATED_PLACEHOLDER.map((g) => (
                <div
                  key={g.name}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "minmax(0,1fr) 64px 64px 56px",
                    gap: 8,
                    fontSize: 12.5,
                    padding: "7px 0",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  <div>{g.name}</div>
                  <div style={{ textAlign: "right" }}>{g.stake}</div>
                  <div style={{ textAlign: "right" }}>{g.pledged}</div>
                  <div style={{ textAlign: "right" }}>{g.qoq}</div>
                </div>
              ))}
            </div>
            <button
              type="button"
              style={{
                marginTop: 14,
                width: "100%",
                minHeight: 46,
                fontFamily: "var(--font-heading)",
                fontWeight: 800,
                fontSize: 12,
                letterSpacing: "0.08em",
                textTransform: "uppercase",
                cursor: "pointer",
                border: 0,
                background: "var(--lr-accent)",
                color: "var(--lr-on-accent)",
              }}
            >
              Unlock company profile
            </button>
            <div style={{ marginTop: 10, fontSize: 11, color: "var(--lr-faint)" }}>
              Shareholding data is not wired yet — this is a placeholder.
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
