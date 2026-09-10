/**
 * One filter definition, rendered twice: as the sticky desktop rail and as
 * the full-screen phone sheet. The `rail` flag is the only difference — it
 * shrinks the controls, since the rail is 250px and the sheet is a whole
 * screen.
 */
import type { Band } from "../data/view";

export type Conf = "all" | "high" | "medium";
export type Listed = "all" | "listed" | "unlisted";

export const BANDS: { k: Band; label: string }[] = [
  { k: "all", label: "Any size" },
  { k: "big", label: "₹2,000cr and up" },
  { k: "mid", label: "₹500–2,000cr" },
  { k: "small", label: "Under ₹500cr" },
  { k: "unknown", label: "Undisclosed" },
];

/** Transcribed from the design's `chip()`. */
export function chipStyle(active: boolean, rail?: boolean): React.CSSProperties {
  return {
    fontFamily: "var(--font-heading)",
    fontWeight: 600,
    fontSize: rail ? 11 : 11.5,
    letterSpacing: "0.06em",
    textTransform: "uppercase",
    cursor: "pointer",
    minHeight: rail ? 34 : 44,
    display: "inline-flex",
    alignItems: "center",
    padding: rail ? "0 10px" : "0 14px",
    whiteSpace: "nowrap",
    border: rail ? `1px solid ${active ? "var(--lr-accent)" : "var(--lr-line-soft)"}` : 0,
    background: active ? (rail ? "var(--lr-accent-soft)" : "var(--lr-accent)") : "transparent",
    color: active ? (rail ? "var(--lr-accent-text)" : "var(--lr-on-accent)") : "var(--lr-muted)",
  };
}

/** Transcribed from the design's `wideChip()` — the full-width size rows. */
export function wideChipStyle(active: boolean, rail?: boolean): React.CSSProperties {
  return {
    fontFamily: "inherit",
    fontSize: rail ? 12.5 : 14,
    cursor: "pointer",
    width: "100%",
    minHeight: rail ? 36 : 48,
    display: "flex",
    alignItems: "center",
    gap: 10,
    textAlign: "left",
    padding: rail ? "0 10px" : "0 12px",
    border: 0,
    borderBottom: "1px solid var(--lr-line-soft)",
    background: active ? "var(--lr-accent-soft)" : "transparent",
    color: active ? "var(--lr-accent-text)" : "var(--lr-muted)",
    fontWeight: active ? 600 : 400,
  };
}

const groupLabel: React.CSSProperties = {
  fontSize: 10.5,
  fontWeight: 600,
  letterSpacing: "0.1em",
  textTransform: "uppercase",
  color: "var(--lr-faint)",
};

export interface FilterState {
  band: Band;
  conf: Conf;
  listed: Listed;
  outlet: string;
  bandCounts: Record<string, number>;
  outlets: string[];
  setBand: (b: Band) => void;
  setConf: (c: Conf) => void;
  setListed: (l: Listed) => void;
  setOutlet: (o: string) => void;
}

export function FilterGroups({ s, rail }: { s: FilterState; rail?: boolean }) {
  return (
    <>
      <div>
        <div style={groupLabel}>Size</div>
        <div style={{ marginTop: rail ? 10 : 11, display: "flex", flexDirection: "column" }}>
          {BANDS.map((b) => (
            <button
              key={b.k}
              type="button"
              onClick={() => s.setBand(b.k)}
              style={wideChipStyle(s.band === b.k, rail)}
            >
              <span>{b.label}</span>
              <span
                style={{
                  marginLeft: "auto",
                  color: "var(--lr-faint)",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {s.bandCounts[b.k] ?? 0}
              </span>
            </button>
          ))}
        </div>
      </div>

      <div>
        <div style={groupLabel}>Confidence</div>
        <div style={{ marginTop: rail ? 10 : 11, display: "flex", flexWrap: "wrap", gap: 6 }}>
          {(["all", "high", "medium"] as Conf[]).map((k) => (
            <button key={k} type="button" onClick={() => s.setConf(k)} style={chipStyle(s.conf === k, rail)}>
              {k === "all" ? "Any" : k}
            </button>
          ))}
        </div>
      </div>

      <div>
        <div style={groupLabel}>Listing</div>
        <div style={{ marginTop: rail ? 10 : 11, display: "flex", flexWrap: "wrap", gap: 6 }}>
          {(["all", "listed", "unlisted"] as Listed[]).map((k) => (
            <button key={k} type="button" onClick={() => s.setListed(k)} style={chipStyle(s.listed === k, rail)}>
              {k === "all" ? "Any" : k}
            </button>
          ))}
        </div>
      </div>

      <div>
        <div style={groupLabel}>Outlet</div>
        <select
          value={s.outlet}
          onChange={(e) => s.setOutlet(e.target.value)}
          aria-label="Filter by outlet"
          style={{
            marginTop: rail ? 10 : 11,
            width: "100%",
            minHeight: rail ? 38 : 46,
            padding: "0 10px",
            font: "inherit",
            fontSize: 13,
            color: "var(--lr-text)",
            background: "var(--lr-inset)",
            border: "1px solid var(--lr-line-soft)",
            borderRadius: 0,
          }}
        >
          {s.outlets.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </div>
    </>
  );
}
