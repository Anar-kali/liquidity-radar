# Liquidity Radar — Site Data Contract

The JSON shape the website consumes. The Claude Design mockups and the frontend
build both work from this document; `export_site.py` is responsible for
producing exactly this and nothing more.

Every figure below was measured against the live `radar.db` on **2026-09-08**:
**601 deals, 1,469 source links, spanning 2026-08-05 → 2026-09-08** (~18 new
deals/day). These are the real distributions, not estimates.

Pipeline sources of truth: `db.py` (`deals`, `deal_members`), `classify.py`
(what Haiku fills in), `sizing.py` (how a size is resolved), `notify.py` (how a
size is rendered today).

---

## 1. `Deal` — the public record

The only object in the public static export.

```ts
type SizeSource =
  | 'stated'          // 184 (31%) — the article stated the figure
  | 'computed'        //   7  (1%) — stake % x market cap, both real numbers
  | 'mcap_plausible'  //  33  (5%) — listed, market cap known, no % to apply
  | 'band'            //  13  (2%) — Haiku range estimate, unlisted company
  | 'unknown'         // 247 (41%) — sizing ran, resolved nothing
  | null;             // 117 (19%) — legacy rows, pre-dating the column

type SizeBand = '100_TO_500' | '500_TO_2000' | 'OVER_2000' | 'UNKNOWN' | null;

interface Deal {
  id:          number;
  company:     string;
  dealType:    DealType;
  confidence:  'high' | 'medium';   // 127 high (21%) / 474 medium (79%)
  confirmed:   boolean;             // exchange-verified

  // ---- size: see §3, this is the subtle part ----
  amountCr:    number | null;       // 256 populated (43%) / 345 null (57%)
  amountRaw:   string | null;       // the phrase behind the figure, verbatim
  sizeSource:  SizeSource;
  sizeBand:    SizeBand;

  oneLine:     string;              // always populated

  // ---- parties ----
  seller:      string | null;       // 164 populated (27%)
  buyer:       string | null;       //  98 populated (16%) — legacy rows only
  individuals: string[];            //  84 non-empty (14%)
  advisers:    Adviser[];           //   0 — field does not exist yet

  // ---- provenance ----
  sources:     Source[];            // 1..12, see §4
  primaryUrl:  string;
  createdAt:   string;              // ISO 8601 UTC
  updatedAt:   string;
}

interface Source {
  outlet:     string;   // "Business Standard", "The Hindu", "BSE" — see §4
  title:      string;   // headline as published, publisher suffix stripped
  url:        string;
  resolvable: boolean;  // false = opaque Google News redirect, see §4
}

interface Adviser {
  firm: string;
  role: 'sellside' | 'buyside' | 'legal' | 'unknown';
}
```

### `DealType` is a real vocabulary — chips are viable

Haiku writes this field as free text, but across 601 deals it has converged to
twelve values. Derive chips from the data at build time, but these are what to
design against:

| Value | Count | | Value | Count |
|---|---|---|---|---|
| `DRHP filing` | 161 | | `promoter sale` | 36 |
| `strategic buyout` | 104 | | `open offer` | 31 |
| `IPO-OFS` | 85 | | `PE secondary` | 29 |
| `block deal` | 78 | | `PE primary` | 26 |
| `other` | 38 | | `unknown` | 10 |
|  |  | | `bulk deal` / `IPO-DRHP` | 3 |

Two of these — `other` and `unknown`, 8% combined — must render without looking
like a bug. Design must also tolerate an unseen value appearing tomorrow.

---

## 2. `CompanyProfile` — the gated record

**Never present in the public export.** Served only through the authenticated
surface, from a separate store.

```ts
interface CompanyProfile {
  company:      string;
  ticker:       string | null;      // "TITAN.NS" — via sizing.py resolution
  listed:       boolean;
  marketCapCr:  number | null;
  asOfQuarter:  string | null;      // "2026-06-30" — filings are quarterly
  promoters:    Promoter[];
  dealIds:      number[];
}

interface Promoter {
  name:        string;
  stakePct:    number;
  pledgedPct:  number | null;       // shares pledged as collateral
  changeQoQ:   number | null;       // percentage-point change vs prior quarter
  contact:     Contact | null;
}

interface Contact {
  registeredOffice: string | null;  // public record (MCA)
  irEmail:          string | null;  // public record
  boardLine:        string | null;  // public record
  personal:         { email: string | null; mobile: string | null } | null;
}
```

`Contact.personal` is split from the public-record fields on purpose: it has a
different source and a different legal basis, and the UI must be able to render
the public half without it.

---

## 3. Size provenance — the one thing the design must not flatten

Every rupee figure is one of six states. They are not interchangeable, and a
public webpage attributes them to you in a way a private Telegram message does
not. `notify.py:alert_amount` already distinguishes them; the site must do at
least as well.

| `sizeSource` | `amountCr` | What to show | Trust |
|---|---|---|---|
| `stated` | always present | `₹2,000cr` | Fact |
| `computed` | always present | `~₹706cr` + "15.26% × mkt cap" | Derived |
| `band` | **always null** | `est. ₹2,000cr+` from `sizeBand` | Estimate |
| `mcap_plausible` | **always null** | "Size undisclosed" | Unknown |
| `unknown` | always null | "Size undisclosed" | Unknown |
| `null` (legacy) | 65 of 117 present | `₹2,000cr`, treat as stated | Fact |

Three traps, all verified in the data:

1. **`sizeBand` is overloaded.** On a `band` row it *is* the estimate. On a
   `stated` row it is a derived bucket of a known figure (74 stated rows carry
   `OVER_2000`). Rendering `sizeBand` as an estimate on a stated row would turn
   a fact into a guess. **Only read `sizeBand` when `sizeSource === 'band'`.**
2. **`amountRaw` exists without `amountCr`.** A `mcap_plausible` row reads
   `"1.66 crore shares — no price stated"` — a share count, not money. Never
   render `amountRaw` as if it were the amount.
3. **`'UNKNOWN'` is a literal string** in `sizeBand`, not a null. 285 rows have
   it. Handle it as absent.

Requirement: the fact/derived/estimate distinction must be legible **in the feed
row**, not only in the detail window. A user scanning 25 rows must never read an
estimate as a fact.

---

## 4. Source links — 67% of them do not point at a publisher

This is the biggest gap between what the detail window is supposed to show
("original website hyperlink") and what the data holds.

- **403 of 601 deal URLs (67%)** and **1,135 of 1,469 source links (77%)** are
  `news.google.com/rss/articles/CBMi…` — opaque Google News redirect tokens.
  The real publisher URL is not recoverable from the token
  (`filters.py:214-222` documents why: the interstitial carries no publisher
  URL and reaching the article needs Google's undocumented batchexecute RPC).
- **`deals.source` is useless as an outlet label** — it reads "Google News" on
  403 rows.
- **But the outlet name *is* recoverable.** Google News appends `" - Publisher"`
  to every headline, and `filters.publisher()` already extracts it with
  `_TRAILING_SOURCE_RE`. The exporter must call that existing helper to populate
  `Source.outlet` and strip the suffix from `Source.title`. Verified against
  deal 757: eight sources resolve to Entrackr, Zee Business, The Hindu, ET
  Retail, Deccan Herald, Business Standard, Mint.

So: **the outlet name is trustworthy, the link destination is not.** Design
accordingly — `resolvable: false` links land on a Google interstitial, and the
UI should say so rather than promising a publisher page it can't deliver.

Source count distribution (1,469 links across 601 deals):

| Sources | Deals | | Sources | Deals |
|---|---|---|---|---|
| 1 | 263 (44%) | | 5 | 16 |
| 2 | 101 | | 6–8 | 19 |
| 3 | 41 | | 9–12 | 11 |
| 4 | 20 | | | |

Clustering also carries real signal worth showing: **8 outlets on one deal means
the market is talking about it.** Source count is a ranking dimension, not just
a list length.

---

## 5. Duplicate deals are visible in the feed

Clustering is time-windowed, so the same transaction can produce several `deals`
rows. Measured: **112 company-name collision groups covering 285 of 601 rows
(47%)**. Some are legitimate follow-ups days apart; some are same-day splits of
one transaction — "Fine Edge", "Fine Edge Engineering", and "Fine Edge
Engineering (Ashok Iron Works engineering business)" are three rows for one
₹2,000cr deal, all created within 90 minutes on 2026-08-07.

The feed will therefore show the same company repeatedly. The design needs an
answer for this — grouping rows by company, a "3 related updates" affordance, or
a timeline view per company. Ignoring it makes the feed look broken.

---

## 6. Null-rate reality — design the sparse case first

| Field | Populated |
|---|---|
| `company`, `dealType`, `oneLine`, `confidence`, `sources` | 100% |
| `amountCr` | 43% |
| `seller` | 27% |
| `confirmed` | small minority |
| `buyer` | 16%, legacy rows only — 0% of new deals |
| `individuals` | **14%** |
| `advisers` | 0% |

The single most common detail window in production is: a company name, a deal
type, one sentence, no amount, no named individual, no buyer, no adviser, one
source link that goes to a Google redirect. **That screen is the product.** A
mockup with every field populated is drawing a state that occurs in roughly 1
deal in 20.
