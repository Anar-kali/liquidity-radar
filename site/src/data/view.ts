/**
 * Pipeline record → the design's view model.
 *
 * This is the only place the two vocabularies meet. The pipeline has six
 * size_source states; the design has four provenance classes. Getting this
 * mapping wrong is the one bug that would publish a false number, so it is
 * isolated here and tested rather than inlined into components.
 */
import type { Deal, FeedDeal, Provenance, SizeBand } from "./types";

/* ── provenance ───────────────────────────────────────────────────────── */

/**
 * six pipeline states → four design classes:
 *   stated                        → stated    (a figure a source states)
 *   computed                      → derived   (stake % × market cap)
 *   band                          → estimate  (a range, unlisted company)
 *   mcap_plausible | unknown | ∅  → none      (no figure exists)
 *
 * Legacy rows (size_source null but an amount present) are already
 * normalised to "stated" by export_site.py, so they never reach here as null.
 */
export function provenanceOf(d: { sizeSource: string | null; amountCr: number | null }): Provenance {
  switch (d.sizeSource) {
    case "stated":
      return "stated";
    case "computed":
      return "derived";
    case "band":
      return "estimate";
    default:
      return "none";
  }
}

/** Underline treatment, verbatim from the design source's `U` map. */
export const PROV_STYLE: Record<Provenance, React.CSSProperties> = {
  stated: {
    textDecoration: "underline",
    textDecorationThickness: "1.5px",
    textUnderlineOffset: "5px",
    textDecorationColor: "color-mix(in srgb, currentColor 55%, transparent)",
  },
  derived: {
    textDecoration: "underline",
    textDecorationStyle: "dashed",
    textDecorationThickness: "1.5px",
    textUnderlineOffset: "5px",
    textDecorationColor: "color-mix(in srgb, currentColor 45%, transparent)",
  },
  estimate: {
    textDecoration: "underline",
    textDecorationStyle: "dotted",
    textDecorationThickness: "1.5px",
    textUnderlineOffset: "5px",
    textDecorationColor: "color-mix(in srgb, currentColor 42%, transparent)",
  },
  none: { color: "var(--lr-faint)", fontStyle: "italic" },
};

/** Drawer explanation, verbatim from the design source's PROV_SENTENCE. */
export const PROV_SENTENCE: Record<Provenance, string> = {
  stated: "Stated in the source. Quotable as a fact.",
  derived:
    "Derived from two real numbers — a disclosed stake percentage against live market cap. Not a figure anyone published.",
  estimate: "An estimated band for an unlisted company. Do not quote it as a figure.",
  none: "Nothing in the record supports a number. Treated as unknown, not as small.",
};

/* ── the figure ───────────────────────────────────────────────────────── */

const BAND_LABEL: Record<string, string> = {
  OVER_2000: "₹2,000cr+",
  "500_TO_2000": "₹500–2,000cr",
  "100_TO_500": "₹100–500cr",
};

/** Midpoint used ONLY for banding and sorting — never displayed. */
const BAND_SORT: Record<string, number> = {
  OVER_2000: 2000,
  "500_TO_2000": 1250,
  "100_TO_500": 300,
};

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export function amountLabel(d: { amountCr: number | null; sizeBand: SizeBand; sizeSource: string | null }): string {
  const prov = provenanceOf(d);
  if (prov === "estimate") return (d.sizeBand && BAND_LABEL[d.sizeBand]) || "Size undisclosed";
  if (d.amountCr === null) return "Size undisclosed";
  const figure = `₹${inr.format(d.amountCr)}cr`;
  // The tilde is the design's marker that arithmetic produced this, not a source.
  return prov === "derived" ? `~${figure}` : figure;
}

const FILING_OUTLETS = /^(SEBI|BSE|NSE)\b/i;
const PCT = /(\d{1,2}(?:\.\d{1,2})?)\s*%/;

/**
 * The always-visible caption under every figure. The point of it is that a
 * reader never has to hover or open the drawer to know what they are looking
 * at, so it says where the number came from in the fewest possible words.
 */
export function provenanceNote(
  d: { amountRaw: string | null; sizeBand: SizeBand; sizeSource: string | null; amountCr: number | null },
  primaryOutlet: string | null,
): string {
  switch (provenanceOf(d)) {
    case "stated":
      return FILING_OUTLETS.test(primaryOutlet ?? "") ? "stated in filing" : "stated in report";
    case "derived": {
      // amount_raw reads "15.26% x mcap ~Rs 4628cr = ~Rs 706cr"
      const pct = d.amountRaw?.match(PCT)?.[1];
      return pct ? `${pct}% × mkt cap` : "stake × mkt cap";
    }
    case "estimate":
      return "estimated band · unlisted";
    default:
      // A share count often sits here ("1.66 crore shares — no price stated").
      // It is not money, so it stays a caption and never becomes the figure.
      if (d.amountRaw) return d.amountRaw.replace(/\s*—\s*/g, ", ").slice(0, 44);
      return "no figure attributable";
  }
}

/** Raw extracted string for the drawer's mono block. */
export function rawPhrase(d: { amountRaw: string | null }): string {
  return d.amountRaw ? `"${d.amountRaw}"` : "";
}

/* ── size band, for the filter rail and sorting ───────────────────────── */

export type Band = "all" | "big" | "mid" | "small" | "unknown";

export function sortValue(d: { amountCr: number | null; sizeBand: SizeBand; sizeSource: string | null }): number | null {
  if (provenanceOf(d) === "estimate") return (d.sizeBand && BAND_SORT[d.sizeBand]) ?? null;
  return d.amountCr;
}

export function bandOf(d: { amountCr: number | null; sizeBand: SizeBand; sizeSource: string | null }): Band {
  const v = sortValue(d);
  if (v === null) return "unknown";
  if (v >= 2000) return "big";
  if (v >= 500) return "mid";
  return "small";
}

/* ── time ─────────────────────────────────────────────────────────────── */

export function minutesAgo(iso: string, now = Date.now()): number {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? Number.MAX_SAFE_INTEGER : Math.max(0, Math.round((now - t) / 60000));
}

/**
 * "12m", "1h 10m", "4h", "3d". The design's dataset never exceeded 9h; days
 * are an extension for a feed that actually spans a month.
 */
export function timeLabel(iso: string, now = Date.now()): string {
  const m = minutesAgo(iso, now);
  if (m === Number.MAX_SAFE_INTEGER) return "—";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) {
    const rem = m % 60;
    return rem ? `${h}h ${String(rem).padStart(2, "0")}m` : `${h}h`;
  }
  const days = Math.floor(h / 24);
  return days < 7 ? `${days}d` : `${Math.floor(days / 7)}w`;
}

export function sourceLabel(n: number): string {
  if (n === 0) return "no article";
  return n === 1 ? "1 source" : `${n} sources`;
}

/* ── people ───────────────────────────────────────────────────────────── */

export function peopleLabel(individuals: string[]): string {
  return individuals.length ? `◆ ${individuals.join(", ")}` : "";
}

/* ── search ───────────────────────────────────────────────────────────── */

export function matchesQuery(d: FeedDeal, q: string): boolean {
  if (!q.trim()) return true;
  const hay = [d.company, d.oneLine, d.seller ?? "", d.individuals.join(" "), d.dealType]
    .join(" ")
    .toLowerCase();
  return q
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((term) => hay.includes(term));
}

/* ── coverage links ───────────────────────────────────────────────────── */

/**
 * A resolvable URL goes straight to the publisher. An opaque Google News
 * token is labelled for what it is — the destination is an interstitial, and
 * the design is explicit that we do not promise a page we cannot deliver.
 */
export function linkFor(s: { url: string; resolvable: boolean }): { href: string; label: string; accent: boolean } {
  return s.resolvable
    ? { href: s.url, label: "open ↗", accent: true }
    : { href: s.url, label: "via Google ↗", accent: false };
}

export type AnyDeal = Deal | FeedDeal;
