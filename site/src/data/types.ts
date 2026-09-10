/**
 * The shape export_site.py emits. Mirrors docs/data-contract.md — change
 * both together.
 */

export type SizeSource =
  | "stated"
  | "computed"
  | "mcap_plausible"
  | "band"
  | "unknown"
  | null;

export type SizeBand = "100_TO_500" | "500_TO_2000" | "OVER_2000" | null;

export interface Source {
  outlet: string;
  title: string;
  url: string;
  /** false = an opaque Google News redirect token, not a publisher page. */
  resolvable: boolean;
  /** When this article joined the cluster. */
  attachedAt: string | null;
}

/** Compact form carried in the feed for the row's inline update list. */
export interface FeedUpdate {
  attachedAt: string | null;
  title: string;
}

export interface Adviser {
  firm: string;
  role: "sellside" | "buyside" | "legal" | "unknown";
}

export interface Deal {
  id: number;
  company: string;
  dealType: string;
  confidence: "high" | "medium";
  confirmed: boolean;
  amountCr: number | null;
  amountRaw: string | null;
  sizeSource: SizeSource;
  sizeBand: SizeBand;
  oneLine: string;
  seller: string | null;
  buyer: string | null;
  individuals: string[];
  advisers: Adviser[];
  listed: boolean;
  ticker: string | null;
  sources: Source[];
  primaryUrl: string;
  createdAt: string;
  updatedAt: string;
}

/** The trimmed projection the feed list renders from. */
export interface FeedDeal
  extends Omit<Deal, "sources" | "advisers" | "buyer" | "primaryUrl" | "ticker"> {
  primaryOutlet: string | null;
  sourceCount: number;
  updates: FeedUpdate[];
}

export interface Feed {
  generatedAt: string;
  dealCount: number;
  totalDeals: number;
  feedWindowDays: number | null;
  deals: FeedDeal[];
}

/**
 * The design's four provenance classes. These are NOT the pipeline's
 * size_source values — see view.ts:provenanceOf for the mapping. Keys match
 * the design source's own (`estimate`, `none`), not the handoff README's
 * prose ("estimated", "undisclosed").
 */
export type Provenance = "stated" | "derived" | "estimate" | "none";

/* ── pattern alerts ───────────────────────────────────────────────────── */

export interface PatternSale {
  date: string;
  valueCr: number;
  /** Which NSE file(s) reported it — "bulk", "block", "pit", "news". */
  sources: string[];
}

/**
 * Several sub-threshold sales by one person in one company that add up over a
 * rolling 90-day window. Not a Deal: there is no article, no buyer and no
 * headline — just a ledger of trades and a running total.
 */
export interface PatternAlert {
  id: number;
  person: string;
  company: string;
  /** Deduplicated. `storedTotalCr` is what the Telegram alert said. */
  totalCr: number;
  storedTotalCr: number;
  saleCount: number;
  weeks: number;
  firstTrade: string;
  lastTrade: string;
  alertedAt: string;
  sales: PatternSale[];
}

export interface PatternFeed {
  generatedAt: string;
  count: number;
  alerts: PatternAlert[];
}
