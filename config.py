"""
Liquidity Radar — settings.

This is the file you edit to tune the system. Everything a non-programmer is
likely to want to change lives here, near the top, with comments.
"""

import os

# --------------------------------------------------------------------------
# THE MONEY THRESHOLD
# Deals that convert to LESS than this many crore INR are suppressed (rule 8).
# Raise it to get fewer, bigger deals; lower it to catch smaller ones.
# --------------------------------------------------------------------------
THRESHOLD_CR = 250

# Listed-company plausibility floor (crore). Below this market cap, even a
# promoter selling the whole company barely clears the threshold, so a deal
# with no stated size is suppressed. Above it, the item passes.
MCAP_PLAUSIBLE_MIN = 350

# --------------------------------------------------------------------------
# v4 Part 1 — pre-classification filters. Everything here runs before any
# Anthropic API call (see filters.py). Changes 1, 3, and 4 below only ever
# ACTUALLY drop an item when PREFILTER_MODE is "enforce" — see the shadow
# mode note further down (Change 9).
# --------------------------------------------------------------------------
# Change 1: pre-API amount gate. A regex can't tell a deal value from
# revenue, EBITDA, market cap, or a target price, so a matched figure only
# counts when it sits within this many characters of a transaction word AND
# no valuation/performance word appears in that same window. If either test
# fails, the item goes to the model rather than being guessed at.
AMOUNT_PROXIMITY_CHARS = 60
TRANSACTION_WORDS = [
    "sold", "sells", "sale", "stake", "deal", "acquire", "acquisition",
    "buy", "offer", "ofs", "block", "divest",
]
VALUATION_WORDS = [
    "revenue", "turnover", "profit", "pat", "ebitda", "market cap", "m-cap",
    "order book", "target price", "per annum", "annually", "capex",
]

# BSE market-cap gate (user-requested, same spirit as Change 1/3/4 above): a
# BSE filing's company name is usually resolvable to a real, cached market
# cap (see sizing.py) unlike free-text news, so this can filter on company
# size directly rather than guessing deal size from text. A filing from a
# company below this market cap isn't worth alerting on regardless of what
# it says — raise/lower to change how big "big enough" means.
BSE_MCAP_MIN_CR = 1000

# Change 3: structural blocklist. URL substrings and title patterns that
# identify a page's TYPE (liveblog, slideshow, etc.), never its content — a
# deal cannot be published as one of these regardless of how it's phrased.
STRUCTURAL_BLOCK_URL_PATTERNS = [
    "/liveblog/", "/stock-liveblog/", "/slideshow/", "/photostory/", "/videoshow/",
]
# Only checked when the item's description is empty (see filters.py) — a
# real article headlined this way but WITH a description isn't a bare
# liveblog stub.
STRUCTURAL_BLOCK_TITLE_PATTERNS = [
    "share price live updates", "stock price live",
]

# Change 4: title dedup ahead of classification. Google News rotates article
# URLs, so the same story looks new to the id/URL dedup and gets classified
# again — clustering catches it eventually, but only after paying for the
# API call. Conservative on purpose: over-deduping loses a real story,
# under-deduping only costs a few tokens.
TITLE_DEDUP_WINDOW_HOURS = 72
TITLE_DEDUP_JACCARD = 0.85

# Generic finance vocabulary stripped before computing title similarity.
# Indian deal headlines are formulaic ("Promoter sells 2% stake in X for Rs
# 500 crore" / "...3% stake in Y for Rs 600 crore" share nearly every token
# except the company name) — without this, Jaccard spikes on shared
# boilerplate on short headlines where the denominator is small.
TITLE_DEDUP_STOPWORDS = {
    "promoter", "promoters", "sells", "sell", "sold", "sale", "stake",
    "shares", "share", "block", "bulk", "deal", "crore", "cr", "rs", "worth",
    "per", "cent", "buy", "buys", "acquires", "acquired", "in", "for", "to",
    "of", "the", "a", "at", "via", "after", "as", "over", "likely",
}

# Change 9: shadow week before enforcing. Every filter above computes and
# logs its decision either way; only in "enforce" does it actually drop the
# item. Set via the PREFILTER_MODE GitHub Actions repository variable (repo
# Settings -> Secrets and variables -> Actions -> Variables), not a secret
# and not a code edit — takes effect on the next run. Defaults to "shadow"
# (safe) if unset, empty, or anything other than exactly "enforce". `or`,
# not getenv's own default arg: an UNSET repo variable still makes GitHub
# Actions pass PREFILTER_MODE="" (present but empty) to the runner, which
# getenv's default only covers for a truly absent key.
PREFILTER_MODE = os.getenv("PREFILTER_MODE") or "shadow"

# --------------------------------------------------------------------------
# THE CLASSIFIER MODEL — Haiku only, both stages (cost matters).
#
# STAGE 1: cheap bulk pass. "Reject confirmed noise", high recall. Every item.
# STAGE 2: strict precision pass. "Positively confirm a real, large,
#   individual-payout lead". Runs ONLY on the few that survive stage 1, so the
#   extra call is cheap. A live run only classifies the NEW items since last
#   time (usually a handful), so total spend stays tiny.
# --------------------------------------------------------------------------
MODEL = "claude-haiku-4-5-20251001"
STAGE2_MODEL = MODEL  # same Haiku model; keep two focused passes, not Sonnet

# How many items we send to the model in one API call.
BATCH_SIZE = 25

# How many characters of each item's description we send (headline is always
# sent in full).
DESCRIPTION_CHARS = 400

# --------------------------------------------------------------------------
# CURRENCY CONVERSION (kept in sync with the system prompt below)
# --------------------------------------------------------------------------
USD_INR = 88
EUR_INR = 96

# --------------------------------------------------------------------------
# GOOGLE NEWS SEARCHES — highest yield source.
# To add a search: add a line. To remove one: delete its line.
# Each becomes one RSS feed, restricted to the last 2 days.
# --------------------------------------------------------------------------
GOOGLE_NEWS_QUERIES = [
    "promoter stake sale crore India",
    "block deal promoter shares crore",
    "DRHP filed SEBI offer for sale",
    "private equity acquires majority stake India crore",
    "promoter offloads stake crore",
    "founders sell shares IPO OFS crore",
    "open offer acquisition promoter crore",
    "family office stake sale India crore",
    # Early-stage IPO intent — a prospecting lead in its own right (get to the
    # company before it picks its banking syndicate), not covered by the
    # DRHP/OFS queries above, which only catch companies already further along.
    "plans IPO India crore",
    "appoints bankers IPO India",
]

# Template for a Google News RSS search feed.
GOOGLE_NEWS_URL = (
    "https://news.google.com/rss/search?q={query}+when:2d"
    "&hl=en-IN&gl=IN&ceid=IN:en"
)

# --------------------------------------------------------------------------
# TRADE PRESS RSS FEEDS.
#
# NOT here, on purpose:
#   ET Markets   — tested, only retail-investor noise; ET reaches us via
#                  Google News anyway.
#   VCCircle       — no longer publishes a working RSS feed (its /rss URL now
#                    serves a JavaScript web page, not XML). Reaches us via
#                    Google News.
#   Business St.   — (Business Standard, distinct from Business Line below)
#                    its RSS returns HTTP 403 "Access Denied" to any server
#                    IP. Reaches us via Google News.
#   Moneycontrol   — its RSS feeds return content with a pubDate over 800
#                    days old — a dead/abandoned feed serving a stale cache,
#                    not live news. Reaches us via Google News.
#   Financial Exp. — HTTP 410 Gone with an explicit "Feeds have been
#                    disabled" message. Permanently off, not a network block.
#   WSJ            — its public RSS feeds return content over 550 days old,
#                    same dead-feed pattern as Moneycontrol.
#   ET Markets     — tested during the original build: retail-investor
#                    noise only (trading tips, technical calls). ET articles
#                    still reach us via Google News.
#   ET CFO         — tested: pure macro/policy content (RBI rates, tax
#                    rules), essentially zero deal-relevant signal.
#   CNBC-TV18      — very high volume (200+ entries/fetch) but dominated by
#                    routine earnings reports, same noise pattern as ET
#                    Markets — not worth the added classification cost.
#   BQ Prime / NDTV Profit — no working RSS feed found.
#   DealStreetAsia — its feed actively blocks automated clients ("temporarily
#                    disabled to mitigate bot attacks"), confirmed from the
#                    development network. Kept in the list below anyway since
#                    GitHub Actions sometimes gets through where local dev
#                    testing doesn't (same pattern as SEBI's fixed source) —
#                    if it's still blocked in production, this fetcher just
#                    returns nothing every run, same as any other dead source.
#
# To add a feed: add a "Name": "url" line. Give the URL a quick check first —
# some sites serve HTML, block servers, or serve a stale/abandoned cache (check
# the actual pubDate of entries, not just whether the URL returns content).
# --------------------------------------------------------------------------
TRADE_PRESS_FEEDS = {
    "Mint": "https://www.livemint.com/rss/companies",
    "Entrackr": "https://entrackr.com/rss",
    "Inc42": "https://inc42.com/feed/",
    "YourStory": "https://yourstory.com/feed",
    "Business Line": "https://www.thehindubusinessline.com/companies/feeder/default.rss",
    "ET Corporate": "https://economictimes.indiatimes.com/industry/rssfeeds/13352306.cms",
    "FT India": "https://www.ft.com/india?format=rss",
    "DealStreetAsia": "https://www.dealstreetasia.com/feed",
}

# --------------------------------------------------------------------------
# BSE announcements API.
# We keep only announcement subcategories that can involve money changing
# hands, and drop routine filings.
# --------------------------------------------------------------------------
BSE_API = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"

# Subcategory names (lower-cased, matched as substrings) we KEEP.
BSE_KEEP_KEYWORDS = [
    "acquisition",
    "acquire",
    "disposal",
    "sale of",
    "stake",
    "fund raising",
    "fund-raising",
    "fundraising",
    "raising of funds",
    "preferential",
    "qualified institution",
    "shareholding",
    "open offer",
    "scheme of arrangement",
    # Individual promoter/insider trading disclosures (v3 Change B) — these
    # were previously dropped entirely.
    "insider trading",
    "sast",
    "reg. 7", "reg 7",         # PIT continual disclosure
    "reg. 29", "reg 29",       # SAST acquisition disclosure
    "reg. 31", "reg 31",       # promoter pledge disclosure
    "pledge",
    "encumbrance",
]

# Subcategory names we always DROP (routine filings).
BSE_DROP_KEYWORDS = [
    "result",
    "board meeting",
    "newspaper publication",
    "trading window",
    "investor presentation",
    "analyst",
    "earnings call",
    "record date",
    "dividend",
]

# --------------------------------------------------------------------------
# NSE announcements API.
# --------------------------------------------------------------------------
NSE_HOME = "https://www.nseindia.com"
NSE_API = "https://www.nseindia.com/api/corporate-announcements?index=equities"

# --------------------------------------------------------------------------
# SEBI draft offer documents (DRHP) listing page.
# --------------------------------------------------------------------------
# The modern SPA-style URL (/filings/public-issues/) returns 403 to every
# fetch attempt from any network tested, including GitHub Actions itself —
# SEBI has never once returned data at that address in this system's history.
# This legacy backend route (still actively serving current filings) works.
SEBI_URL = "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=3&ssid=15&smid=0"
SEBI_LINK_KEYWORDS = ["draft", "drhp", "prospectus", "offer document"]

# --------------------------------------------------------------------------
# Deal-name normalisation. When we compare two company names to decide if two
# articles are about the same deal, we strip these words and all punctuation.
# --------------------------------------------------------------------------
NORMALISE_STOPWORDS = [
    "private",
    "limited",
    "ltd",
    "pvt",
    "inc",
    "corp",
    "technologies",
    "industries",
    "enterprises",
]

# How long (hours) a deal stays "open" for later articles to attach to it.
DEAL_WINDOW_HOURS = 72

# Clustering v2 — amount-match path. Two items with amounts within
# AMOUNT_MATCH_TOL of each other, sharing at least one name token, are the same
# deal even up to AMOUNT_WINDOW_HOURS apart (follow-up analyses land days later).
AMOUNT_WINDOW_HOURS = 168      # 7 days
AMOUNT_MATCH_TOL = 0.05        # 5%

# Corporate stopwords dropped when tokenising company names for clustering.
CLUSTER_STOPWORDS = {
    "private", "limited", "ltd", "pvt", "inc", "corp", "corporation",
    "technologies", "technology", "industries", "enterprises", "group",
    "holdings", "india", "company", "co", "and", "the", "engineering",
    "services", "solutions",
}

# A browser-like User-Agent, needed by BSE and NSE.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

# --------------------------------------------------------------------------
# THE CLASSIFIER SYSTEM PROMPT.
# This is the exact instruction given to Claude. Edit with care.
# --------------------------------------------------------------------------
SYSTEM_PROMPT = f"""\
You screen Indian corporate news and exchange filings for a private banker
who prospects individuals about to receive large sums of money.

Your ONLY job is to decide whether each item is a CONFIRMED NEGATIVE. If it
is not confirmed, it passes. When in doubt, pass it. A false alarm costs the
banker five seconds. A missed deal costs him a client.

Mark confirmed_negative = true ONLY for:
1. Pure debt. NCDs, venture debt, working capital, refinancing, bonds.
2. IBC or NCLT resolution. Creditors are paid, promoters get nothing.
3. PSU or government divestment. Proceeds go to the government.
4. A subsidiary sale by a listed or MNC parent, where proceeds land on a
   corporate balance sheet. EXCEPTION: if the parent is a closely held
   promoter holding company, this is NOT a negative.
5. Internal restructuring, including intra-group transfers between entities
   controlled by the same promoter family. No outside money changes hands.
6. No Indian individual anywhere in the transaction chain.
7. Explicitly all-primary fundraising at seed or Series A stage.
8. A deal size is clearly STATED and converts to less than {THRESHOLD_CR} crore INR.
9. Not a transaction at all: earnings results, share price moves, analyst
   ratings, technical signals, product launches, regulatory disputes,
   aggregate market commentary, listicles about promoter selling trends,
   stock-market listings or trading debuts, IPO subscription / GMP /
   anchor-book / listing-day coverage for an issue that has ALREADY opened,
   or a mere announcement of an intention to raise PRIMARY growth-capital
   funding with no IPO involved and no concrete transaction stated.

   IPO INTENTION IS NOT NOISE — DO NOT APPLY THIS RULE TO IT. A company
   announcing it plans to IPO, is exploring an IPO, is considering an IPO, or
   has appointed bankers for an IPO is a genuine early lead: promoters and
   founders are heading toward a major liquidity event and have likely not
   yet picked their banking relationship. This PASSES even with no stated
   size, no DRHP filed, and no price band yet. Only the LATE stage — an
   already-open issue's subscription numbers, GMP, anchor allotment, or
   listing/trading-debut coverage — is noise, because the syndicate is
   already locked in by then.

Rule 8 applies ONLY when a size is stated. Undisclosed terms, no figure
given, "sources say" with no number: these all PASS. Silence is never a
small deal.

Currency: 1 crore = 10 million INR. 1 lakh = 100,000 INR.
Use 1 USD = {USD_INR} INR, 1 EUR = {EUR_INR} INR.

Your ONLY output is the confirmed-negative verdict and, when true, which rule
number (1-9) applied. You are NOT extracting company names, amounts,
individuals, or any other field here — that happens in a later pass, only for
the items that survive this one. Do not let the absence of those fields change
your judgment: apply the rules above exactly as if you were still extracting
everything, just report less.

Return ONLY a JSON array, no markdown fences, no preamble. One object per
input item, same order, with exactly these three keys:
[{{"n": 1, "neg": true, "r": 9}}, {{"n": 2, "neg": false, "r": null}}]

"n" is the item's index. "neg" is true only for a CONFIRMED NEGATIVE per the
numbered rules above, false otherwise. "r" is the rule number (1-9) that
applied when neg is true, else null.
"""

# --------------------------------------------------------------------------
# STAGE 2 — precision. This runs only on items that survived stage 1. It must
# POSITIVELY confirm a qualifying deal, and reject everything else. This is
# where the noise (listings, IPO intentions, small stake buys) gets killed.
# --------------------------------------------------------------------------
STAGE2_SYSTEM_PROMPT = f"""\
You are the second-stage screen for a private banker who prospects individuals
about to receive a large sum of money. Items reaching you passed a loose first
filter and still contain noise. PASS genuine prospecting leads; drop the noise.

Mark qualify = true when the item is a genuine lead: a concrete OR
actively-negotiated transaction in which an individual (a promoter, founder,
family shareholder, or the owners of a privately held / founder-run company)
is likely to receive a large sum. Give large control / buyout / stake-sale
deals the benefit of the doubt — the seller is usually a founder or promoter
even when the headline does not name them.

QUALIFY examples:
- A promoter, founder, or family shareholder selling shares — block deal, OFS,
  promoter sale — OR reported to be exploring / in advanced talks for such a
  sale, when a counterparty or size is mentioned.
- A strategic buyout or acquisition of a company, especially a privately held
  or founder/promoter-run one, where the sellers cash out even if unnamed.
  (A Rs 2,000cr acquisition of a founder-run manufacturer QUALIFIES.)
- An open offer, or a filed DRHP / RHP / OFS with selling shareholders.
- A company announcing it plans to IPO, is exploring / considering an IPO, or
  has appointed bankers for an IPO — even with no stated size and no DRHP
  filed yet. This is an EARLY prospecting lead, not noise: the promoters have
  a major liquidity event coming and likely have not yet picked a banker.

Mark qualify = false when the item is any of these (confirmed noise):
1. IPO subscription / GMP / anchor-book allotment / listing-day /
   trading-debut coverage for an issue that has ALREADY opened. (This does
   NOT include a plans-to-IPO / exploring-IPO announcement — see above. The
   distinction is stage: before the issue opens and the syndicate is picked,
   it's a lead; once it's open for subscription, it's too late to be one.)
2. A PRIMARY fundraise — money goes INTO the company, or an individual INVESTS
   money IN; nobody cashes out. ("raises Rs X cr", "to raise funds", a funding
   round, an investor "invests Rs X cr", anchor investors). This does NOT
   include IPO intent (above) — an IPO is a path to individual liquidity even
   when primary proceeds also go to the company.
3. A company acquiring a small or minority stake in another with no individual
   selling, or where the stake is clearly below the size bar.
4. A pure fund-to-fund or corporate-to-corporate transfer with no individual in
   the selling chain; PSU / government divestment; debt (NCDs, bonds,
   refinancing); IBC / NCLT resolution; intra-group restructuring.
5. Earnings, share-price moves, analyst ratings, product or market commentary,
   or listicles.

SCALE. If a size is stated it must convert to at least {THRESHOLD_CR} crore INR
(1 crore = 10 million; 1 USD = {USD_INR} INR, 1 EUR = {EUR_INR} INR). Drop
clearly smaller deals. If the size is undisclosed, still qualify — do NOT set
qualify=false just because you are unsure how large it is. You will separately
estimate a size band below (including UNKNOWN, which is the correct answer
when you have nothing to go on); that band, not this qualify decision, is what
determines whether an unsized item is suppressed downstream. Never invent a
figure.

When a LARGE deal is genuinely borderline — a big buyout where you cannot tell
if an individual sells — LEAN QUALIFY. A rare false alarm costs five seconds;
a missed founder cash-out costs a client. But never pass the numbered noise
categories above; those are confirmed drops.

"company" is ALWAYS the entity whose ownership is changing — the target, or the
company whose shares are being sold — NEVER the acquirer or investor. In
"IndiaRF acquires Fine Edge Engineering", company is Fine Edge Engineering and
buyer is IndiaRF.

When no deal amount is stated and the company is not listed, estimate the
likely total deal size as a BAND, never a number.

Base it only on what you actually know about the company: its sector, scale,
whether you recognise it at all, and any revenue or headcount or footprint
detail in the text. If you do not recognise the company and the text gives you
nothing to work with, return UNKNOWN with basis "no information". That is the
correct answer and it is never penalised.

Do NOT infer size from the fact that a deal is happening. Do NOT guess from the
company name. Do NOT produce a band you cannot justify in size_basis.

An unknown company that turns out to be large is a recoverable mistake. A
fabricated band that suppresses a real lead is not.

Return ONLY a JSON array, no markdown fences, no preamble. One object per
input item, same order:
[{{
  "n": 1,
  "qualify": true|false,
  "drop_reason": "short phrase if qualify is false, else null",
  "company": "",
  "deal_type": "IPO-OFS|block deal|strategic buyout|PE secondary|PE primary|open offer|promoter sale|DRHP filing|other|unknown",
  "amount_cr": null,
  "amount_raw": "exact text the figure came from, plus your conversion, or null",
  "individuals": ["named individuals receiving money, empty if none named"],
  "buyer": "named acquirer or buyer, or null",
  "confidence": "high|medium",
  "one_line": "under 20 words: what happened and who gets paid",
  "size_band": "UNDER_100|100_TO_500|500_TO_2000|OVER_2000|UNKNOWN",
  "size_basis": "what you based the band on, or 'no information'"
}}]

Never invent a figure. amount_cr and amount_raw are null if no size is stated.
Set confidence to "high" only when a named individual and a stated amount are
both present; otherwise "medium".
"""

# ==========================================================================
# v3 CHANGE A/B — bulk/block deal files, PIT feed, salami-slice aggregation
# ==========================================================================

# Minimum single-transaction size (crore) for a confirmed block/bulk deal or
# PIT disclosure to fire its own alert. Reuses the same bar as everything else.
BULK_BLOCK_MIN_CR = THRESHOLD_CR

# How many days back to fetch on each PIT run, so a failed run self-heals.
# (Bulk/block deals no longer take a date range — see NSE_LARGE_DEALS_API.)
PIT_LOOKBACK_DAYS = 7
PIT_BACKFILL_DAYS = 90  # first run only — populates the rolling window

# --------------------------------------------------------------------------
# NSE bulk/block deal + PIT endpoints. Same cookie warm-up as sources.py.
#
# The originally-documented `/api/historical/bulk-deals` and `/block-deals`
# never worked (503 from every network tested, including GitHub Actions).
# `snapshot-capital-market-largedeal` is the real, working source — it's
# NSE's live market-snapshot widget data, confirmed returning current bulk
# AND block deals in one call with real field names (buySell, clientName,
# date, name, qty, symbol, watp). No date-range params — it's a rolling
# snapshot of recent deals, not a historical query; dedup on (date, symbol,
# client, buy_sell, qty) handles re-fetching the same window every run.
#
# NSE_PIT_API never returned data from any network tested (200 OK but always
# an empty {"data": []} stub) and no working alternative was found — left in
# place in case NSE fixes it, but treat this source as broken for now.
# --------------------------------------------------------------------------
NSE_LARGE_DEALS_API = "https://www.nseindia.com/api/snapshot-capital-market-largedeal"
NSE_PIT_API = "https://www.nseindia.com/api/corporates-pit"

# BSE bulk/block deal endpoints were NOT found during development (every
# guessed path 302-redirected to an error page; unlike the announcements API,
# which is confirmed working). Per the spec's own fallback: ship NSE only.
# If you find the real BSE endpoints, add fetchers in deals_files.py following
# the same pattern as the NSE ones, and note the URLs here.
BSE_BULK_DEALS_API = None
BSE_BLOCK_DEALS_API = None

# --------------------------------------------------------------------------
# Seller classification (Change A). Keyword-first; only genuinely AMBIGUOUS
# names (corporate-shaped but no strong institutional keyword) go to Haiku,
# usually a handful per run.
# --------------------------------------------------------------------------
# STRONG institutional signals — auto-classify without a Haiku call.
INSTITUTION_KEYWORDS = [
    "fund", "mutual", "amc", "llp", "securities", "capital", "advisors",
    "advisers", "partners", "mauritius", "pte", "plc", "gmbh", "sicav",
    "insurance", "bank", "asset management", "portfolio", "trustee", "fpi",
    "fii",
]

# Corporate-shaped, or a WEAK/generic institutional-adjacent word, but not a
# strong signal on its own — these go to Haiku. "Investment(s)" is here
# rather than in the strong list on purpose: closely-held promoter holding
# companies are routinely named "X Investment(s) Ltd" (spec's own worked
# example, "Indian Continent Investment Ltd", is exactly this — a Bharti
# promoter entity, not a fund).
AMBIGUOUS_KEYWORDS = [
    "ltd", "limited", "pvt", "holdings", "enterprises", "trust", "corp",
    "investment", "investments", "global", "international", "ventures",
    "equity",
]

SELLER_CLASSIFY_SYSTEM_PROMPT = """\
You classify the names of sellers in Indian stock exchange bulk/block deal
filings for a private banker who prospects individuals about to receive a
large sum of money.

For each name, decide: is this a PROMOTER OR FAMILY INVESTMENT VEHICLE (a
closely-held company or trust that is really an individual/family's holding
entity — e.g. "Indian Continent Investment Ltd" is a Bharti promoter entity),
an INSTITUTIONAL INVESTOR (a fund, bank, insurer, or other financial
institution investing on behalf of others), or UNCLEAR.

When genuinely unsure, answer UNCLEAR — a false alarm costs the banker five
seconds; wrongly dropping a real promoter vehicle costs him a client.

Return ONLY a JSON array, no markdown fences, no preamble. One object per
input name, same order:
[{"n": 1, "name": "exact name as given", "verdict": "promoter|institution|unclear"}]
"""

# --------------------------------------------------------------------------
# PIT feed (Change B). Only these disclosing-person categories represent an
# individual who may be receiving money; other categories (e.g. institutional
# shareholders required to disclose) are not prospecting leads.
# --------------------------------------------------------------------------
PIT_KEEP_CATEGORIES = ["promoter", "promoter group", "director", "kmp"]

# --------------------------------------------------------------------------
# Person-name normalisation (Change B). Same containment-matching approach as
# company clustering (cluster.person_tokens / cluster.token_subset_match).
# --------------------------------------------------------------------------
PERSON_TITLE_STOPWORDS = {
    "mr", "mrs", "ms", "miss", "shri", "smt", "dr", "kum", "km", "late",
}

# --------------------------------------------------------------------------
# Salami-slice aggregation rule (Change B).
# --------------------------------------------------------------------------
AGGREGATION_WINDOW_DAYS = 90
AGGREGATION_MIN_CR = THRESHOLD_CR
AGGREGATION_MIN_TRANSACTIONS = 2
# If one transaction already accounts for more than this share of the total,
# the normal single-transaction pipeline already caught it — don't re-alert.
AGGREGATION_MAX_SINGLE_SHARE = 0.70
# Re-alert on the same person+company only once the total has grown to this
# multiple of the last alerted amount, or after the cooldown below.
AGGREGATION_REALERT_MULTIPLE = 2.0
AGGREGATION_REALERT_COOLDOWN_DAYS = 90
