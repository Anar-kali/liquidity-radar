# Design Brief — Liquidity Radar

**For:** Claude Design
**Companion document:** `data-contract.md` (field shapes, real null-rates)
**Companion data:** `sample-deals.json` — 33 real deals covering every state
below. Please draw against this file, not invented content.

---

## 1. What this is

An early-warning deal-intelligence site for Indian markets. A backend pipeline
watches financial news, BSE/NSE filings and SEBI IPO papers around the clock,
and identifies transactions where **an individual is about to receive a large
sum of money** — a promoter selling a stake, a founder cashing out at IPO, a
family shareholder exiting.

The audience is one kind of person: **an investment banker prospecting those
individuals.** They are scanning for a name and a number, fast, several times a
day, often on a phone between meetings. Everything about the design should
serve scanning speed and trust in the number.

**Reference points:** Mergermarket for the deal-news feed (dense, professional,
headline-first). PrivateCircle for the company profile (shareholding tables,
promoter records). This product is the hinge between them — a news feed where
clicking a headline opens the deal record *and* the company behind it.

**Volume:** ~18 new deals/day, 601 in the last month. This is a feed a person
reads to the bottom, not an infinite scroll.

---

## 2. The two hard problems

Please treat these as the design brief's actual content. The layout is
comparatively easy; these two are where the product is won or lost.

### Problem 1 — Most deals are mostly empty

The most common record in production is: a company name, a deal type, one
sentence, **no amount, no named individual, no buyer, no adviser**, and one
source link. Measured across 601 real deals:

- 57% have **no deal size**
- 73% have **no named seller**
- 86% have **no named individual**
- 100% have **no adviser** (the pipeline doesn't extract them yet)

A mockup with every field filled describes about 1 deal in 20. **Please design
the sparse case as the primary state and the rich case as the exception.** The
sparse detail window must feel deliberate and finished — not like a page that
failed to load. Absence is information here: "seller not yet named" is a
legitimate, useful thing for a banker to know, and it should read that way.

### Problem 2 — A number is not just a number

Every rupee figure on this site arrived by one of four routes, and conflating
them is the one mistake that would genuinely damage the product's credibility:

| | Route | Show as | Frequency |
|---|---|---|---|
| **Fact** | The article stated it | `₹2,000cr` | 31% |
| **Derived** | stake % × market cap, both real | `~₹706cr` · *15.26% × mkt cap* | 1% |
| **Estimate** | AI range estimate, unlisted company | `est. ₹2,000cr+` | 2% |
| **Unknown** | Nothing to go on | *Size undisclosed* | 57% (of which many still show a company size signal) |

This distinction must be legible **in the feed row itself**, while scanning at
speed — not hidden in a tooltip or revealed only on click. A banker who quotes
an AI estimate to a client as a stated fact has been failed by the interface.

Please invent a visual language for fact / derived / estimate / unknown that
survives being seen 25 times on one screen without becoming noise. This is the
single most valuable thing this design can contribute.

---

## 3. Surfaces to design

### A. The Feed — primary, public

A dense scannable list. Target ~15–25 rows visible per desktop screen. Each row
carries:

- confidence indicator (high 21% / medium 79%)
- **company name** — the primary scan target
- deal-type chip (12 known values: `DRHP filing`, `strategic buyout`, `IPO-OFS`,
  `block deal`, `promoter sale`, `open offer`, `PE secondary`, `PE primary`,
  `bulk deal`, `other`, `unknown` — note the last two must not look broken)
- **amount + provenance** (see Problem 2)
- one-line summary
- named individuals, when present — these are the point of the product, so when
  a name exists it should pull the eye
- outlet + relative time, and a source count when > 1

**Filter rail** (sticky): date range, deal type, amount band, confidence,
listed/unlisted, source outlet. Plus full-text search.

**Sorting worth supporting:** newest, largest amount, and *most covered* —
8 outlets on one deal means the market is talking about it, which is signal.

**Repetition problem:** the same company legitimately appears several times as a
story develops (47% of rows fall into a company-name collision group; one
₹2,000cr deal produced three rows within 90 minutes). The feed will look
repetitive unless the design answers this — grouping by company, a "3 related
updates" affordance, or a per-company timeline. Your call which, but please
solve it.

### B. The Detail Window — the centrepiece

Opens on clicking a headline. **A right-side drawer over the feed**, so feed
context is never lost, and deep-linkable at `/deal/:id`. Sections, in order:

1. **Header** — company, deal type, confidence, exchange-verified badge when
   present, last-updated
2. **Quantum** — the number, large, with its provenance treatment and, in
   smaller type, the raw phrase it came from (`"15.26% × mcap ~Rs 4628cr"`).
   The banker must be able to check the figure against its derivation.
3. **Parties** — seller (who receives the money), individuals being paid, buyer
4. **Advisers** — sellside / buyside / legal. **Empty on every deal today.**
   Please design the empty state as the default and the populated state as
   future. If you conclude the section shouldn't render at all when empty, say
   so — that's a valid answer.
5. **Coverage** — every article that clustered into this deal, as outlet +
   headline + outbound link. 1–12 of them; 44% of deals have exactly one.
   **Caveat that affects this section: 77% of these links are opaque Google News
   redirects, not publisher URLs.** The outlet name is reliable; the destination
   is a Google interstitial. The UI should be honest about that rather than
   promising a publisher page it can't deliver.
6. **Company** — locked teaser for the gated shareholding data (see D)

### C. Empty, loading and edge states — please draw these explicitly

- Feed loading
- Feed empty / no results after filtering
- **The maximally sparse detail window** — no amount, no individual, no seller,
  no buyer, no adviser, one unresolvable source. This is the most-viewed screen
  in the product.
- **A very long company name.** The `company` field sometimes holds an entire
  headline — the real maximum is **116 characters**:
  `"Rs 58,000 crore selloff by promoters, PE funds hits stock market. Why are
  they cashing out now? - The Economic Times"`. 3% of rows exceed 40 chars.
  Company name is the primary scan target, so the truncation rule matters.
- A long deal-type chip
- **A deal with no sources at all** — 18% of deals have no clustered articles,
  leaving only the primary link
- **A deal with 39 sources** (Zetwerk is real) — the Coverage list needs a
  collapse-after-N affordance

### D. Company Profile — gated, design lightly

Behind login. Promoter shareholding table: name, stake %, pledged %, QoQ change,
contact. Plus every deal that company has appeared in. This data doesn't exist
in the pipeline yet, so a directional layout is enough — but the **locked teaser
inside the public detail window** matters and should be designed properly, since
it's the conversion surface.

---

## 4. Constraints

- **Light and dark**, both first-class.
- **Dense data typography.** Tabular figures for every rupee amount so columns
  align while scanning. Indian numbering conventions — crore, `₹1,256cr`,
  comma grouping as `1,00,000` where a full figure is spelled out.
- **Mobile is not secondary.** A banker reads these on a phone between meetings.
  The drawer must work as a full-screen sheet under ~768px, and the feed row
  must stay scannable at that width.
- **Restrained.** This is a professional intelligence tool competing with
  Bloomberg-terminal habits, not a consumer news app. Trustworthy and quiet
  beats vivid. Colour should carry meaning — confidence, provenance — and
  almost nothing else.
- **Deliverable:** design tokens as CSS custom properties, so the build can map
  the system onto components without rewriting them.

---

## 5. Out of scope for this pass

Auth screens, admin/CRM surfaces, email digests, and the contact-management UI.
Feed + detail window + the gated teaser is the whole of this round.
