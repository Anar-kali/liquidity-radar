/**
 * Deal drawer — decide whether to act: is the number real, who gets paid,
 * what is the coverage.
 *
 * Two rules from the handoff that look like omissions but are not:
 *  - Absence is STATED, never blank. An empty seller gets a sentence.
 *  - Advisers do not render at all. The upstream field is 0% populated, and
 *    an empty adviser row would advertise the gap.
 */
import { useEffect, useState } from "react";
import type { Deal } from "../data/types";
import {
  PROV_SENTENCE,
  PROV_STYLE,
  amountLabel,
  linkFor,
  provenanceOf,
  rawPhrase,
  timeLabel,
} from "../data/view";
import { GhostButton, OutlineButton, sectionLabel } from "./controls";

const GATED_PLACEHOLDER = [
  { name: "Promoter family trust", stake: "41.20%", pledged: "0.00%", qoq: "−1.4" },
  { name: "Founder (individual)", stake: "12.80%", pledged: "2.10%", qoq: "−0.6" },
  { name: "Family holding vehicle", stake: "7.35%", pledged: "0.00%", qoq: "0.0" },
  { name: "Promoter group — others", stake: "3.90%", pledged: "0.00%", qoq: "+0.2" },
];

const rule = { height: 1, background: "var(--lr-line-soft)", margin: "22.4px 0" } as const;

export function Drawer({ deal, onClose, now }: { deal: Deal; onClose: () => void; now: number }) {
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
      <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 40 }} />
      <div className="lr-drawer" role="dialog" aria-label={deal.company}>
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 11.2,
            padding: "16.8px 22.4px",
            borderBottom: "1px solid var(--lr-line)",
            position: "sticky",
            top: 0,
            background: "var(--lr-raise)",
            zIndex: 2,
          }}
        >
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8.4, marginBottom: 5.6, flexWrap: "wrap" }}>
              <span
                style={{
                  fontSize: 11,
                  padding: "1.4px 5.6px",
                  borderRadius: 4,
                  background: "var(--lr-chip)",
                  color: "var(--lr-muted)",
                }}
              >
                {deal.dealType}
              </span>
              <span style={{ fontSize: 11, color: "var(--lr-muted)" }}>
                {deal.confidence === "high" ? "High confidence" : "Medium confidence"}
              </span>
              {deal.confirmed && (
                <span style={{ fontSize: 11, color: "var(--lr-accent-text)" }}>exchange-verified</span>
              )}
              <span style={{ fontSize: 11, color: "var(--lr-faint)" }}>
                updated {timeLabel(deal.updatedAt, now)}
              </span>
            </div>
            <div style={{ fontSize: 21, fontWeight: 500, lineHeight: 1.24, textWrap: "pretty" }}>
              {deal.company}
            </div>
          </div>
          <GhostButton onClick={onClose}>Close</GhostButton>
        </div>

        <div style={{ padding: 22.4 }}>
          {/* ── quantum ─────────────────────────────────────────────── */}
          <div style={{ ...sectionLabel, marginBottom: 8.4 }}>Quantum</div>
          <div style={{ fontSize: 34, lineHeight: 1.1, fontVariantNumeric: "tabular-nums", ...PROV_STYLE[prov] }}>
            {amountLabel(deal)}
          </div>
          <div style={{ fontSize: 12, color: "var(--lr-muted)", marginTop: 8.4 }}>{PROV_SENTENCE[prov]}</div>
          {raw && (
            <div
              style={{
                marginTop: 8.4,
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                fontSize: 11.5,
                color: "var(--lr-faint)",
                background: "var(--lr-surface)",
                border: "1px solid var(--lr-line-soft)",
                borderRadius: 8,
                padding: 8.4,
                wordBreak: "break-word",
              }}
            >
              {raw}
            </div>
          )}

          <div style={rule} />

          {/* ── who gets paid ───────────────────────────────────────── */}
          <div style={{ ...sectionLabel, marginBottom: 11.2 }}>Who gets paid</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 11.2 }}>
            <Field label="Individuals">
              {deal.individuals.length ? (
                <span style={{ fontSize: 15, color: "var(--lr-accent-text)" }}>
                  {deal.individuals.join(", ")}
                </span>
              ) : (
                <span style={{ fontSize: 13, color: "var(--lr-faint)" }}>
                  No individual named yet — the pipeline records one in 14% of deals
                </span>
              )}
            </Field>
            <Field label="Selling entity">
              {deal.seller ? (
                <span style={{ fontSize: 14 }}>{deal.seller}</span>
              ) : (
                <span style={{ fontSize: 13, color: "var(--lr-faint)" }}>
                  Selling entity not disclosed in any source so far
                </span>
              )}
            </Field>
            {deal.buyer && (
              <Field label="Buyer">
                <span style={{ fontSize: 14 }}>{deal.buyer}</span>
              </Field>
            )}
          </div>

          <div style={rule} />

          {/* ── coverage ────────────────────────────────────────────── */}
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 11.2 }}>
            <div style={sectionLabel}>Coverage</div>
            <div style={{ fontSize: 11.5, color: "var(--lr-faint)" }}>
              {withTitles.length === 0
                ? "no press coverage"
                : `${withTitles.length} ${withTitles.length === 1 ? "article" : "articles clustered"}`}
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
            {shown.map((s, i) => {
              const link = linkFor(s);
              return (
                <a key={i} className="lr-src" href={link.href} target="_blank" rel="noopener noreferrer">
                  <div style={{ minWidth: 0 }}>
                    <div
                      className="lr-src-h lr-clamp2"
                      style={{ fontSize: 13, lineHeight: 1.45, color: "var(--lr-text)" }}
                    >
                      {s.title}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--lr-faint)", marginTop: 2.8 }}>
                      {s.outlet}
                      {s.attachedAt ? ` · ${timeLabel(s.attachedAt, now)}` : ""}
                    </div>
                  </div>
                  <div
                    style={{
                      fontSize: 11,
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
            <OutlineButton onClick={() => setAllSources(true)} style={{ marginTop: 11.2 }}>
              Show all {withTitles.length} articles
            </OutlineButton>
          )}

          {withTitles.length === 0 && (
            <div style={{ fontSize: 13, color: "var(--lr-muted)", padding: "8.4px 0" }}>
              Picked up from an exchange filing, not from press. No article to open.
            </div>
          )}

          <div style={{ fontSize: 11, color: "var(--lr-faint)", marginTop: 11.2, lineHeight: 1.6 }}>
            Most links arrive as Google News redirect tokens — the outlet is reliable, the destination is an
            interstitial. Marked accordingly rather than promising a page we can't deliver.
          </div>

          <div style={rule} />

          {/* ── gated company profile ───────────────────────────────── */}
          <div
            style={{
              border: "1px solid var(--lr-line)",
              borderRadius: 14,
              padding: 16.8,
              background: "var(--lr-surface)",
              position: "relative",
              overflow: "hidden",
            }}
          >
            <div style={{ ...sectionLabel, marginBottom: 8.4 }}>Company profile</div>
            <div style={{ fontSize: 14, marginBottom: 11.2 }}>
              Promoter shareholding, pledges and quarter-on-quarter change
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(0,1fr) 64px 64px 64px",
                gap: 8.4,
                fontSize: 11,
                color: "var(--lr-faint)",
                paddingBottom: 5.6,
                borderBottom: "1px solid var(--lr-line-soft)",
              }}
            >
              <div>Holder</div>
              <div style={{ textAlign: "right" }}>Stake</div>
              <div style={{ textAlign: "right" }}>Pledged</div>
              <div style={{ textAlign: "right" }}>QoQ</div>
            </div>
            <div
              aria-hidden
              style={{
                filter: "blur(4.5px)",
                opacity: 0.55,
                userSelect: "none",
                pointerEvents: "none",
                marginTop: 5.6,
              }}
            >
              {GATED_PLACEHOLDER.map((g) => (
                <div
                  key={g.name}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "minmax(0,1fr) 64px 64px 64px",
                    gap: 8.4,
                    fontSize: 12.5,
                    padding: "5.6px 0",
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
            <div style={{ display: "flex", alignItems: "center", gap: 11.2, marginTop: 14, flexWrap: "wrap" }}>
              <OutlineButton>Unlock company profile</OutlineButton>
              <span style={{ fontSize: 11.5, color: "var(--lr-faint)" }}>
                Shareholding data not yet wired — this is a placeholder
              </span>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "96px minmax(0,1fr)", gap: 11.2, alignItems: "baseline" }}>
      <div style={{ fontSize: 12, color: "var(--lr-faint)" }}>{label}</div>
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  );
}
