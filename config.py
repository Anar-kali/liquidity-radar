"""
Liquidity Radar — settings.

This is the file you edit to tune the system. Everything a non-programmer is
likely to want to change lives here, near the top, with comments.
"""

# --------------------------------------------------------------------------
# THE MONEY THRESHOLD
# Deals that convert to LESS than this many crore INR are suppressed (rule 8).
# Raise it to get fewer, bigger deals; lower it to catch smaller ones.
# --------------------------------------------------------------------------
THRESHOLD_CR = 250

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
#   VCCircle     — no longer publishes a working RSS feed (its /rss URL now
#                  serves a JavaScript web page, not XML). VCCircle stories
#                  still reach us via Google News.
#   Business St. — its RSS returns HTTP 403 "Access Denied" to any server IP
#                  (anti-bot block), so it can't be fetched from GitHub
#                  Actions. Business Standard stories reach us via Google News.
#
# To add a feed: add a "Name": "url" line. Give the URL a quick check first —
# some sites serve HTML or block servers.
# --------------------------------------------------------------------------
TRADE_PRESS_FEEDS = {
    "Mint": "https://www.livemint.com/rss/companies",
    "Entrackr": "https://entrackr.com/rss",
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
SEBI_URL = "https://www.sebi.gov.in/filings/public-issues"
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
   listing-day coverage, or a mere announcement of an INTENTION to launch an
   IPO or raise funds where no concrete transaction, DRHP, or selling
   shareholder is stated.

Rule 8 applies ONLY when a size is stated. Undisclosed terms, no figure
given, "sources say" with no number: these all PASS. Silence is never a
small deal.

Currency: 1 crore = 10 million INR. 1 lakh = 100,000 INR.
Use 1 USD = {USD_INR} INR, 1 EUR = {EUR_INR} INR. Show your working in amount_raw.

"company" is ALWAYS the entity whose ownership is changing — the target, or the
company whose shares are being sold — NEVER the acquirer or investor. In
"IndiaRF acquires Fine Edge Engineering", company is Fine Edge Engineering and
buyer is IndiaRF.

Return ONLY a JSON array, no markdown fences, no preamble. One object per
input item, same order:
[{{
  "n": 1,
  "confirmed_negative": true|false,
  "negative_reason": "rule number and short phrase, or null",
  "company": "",
  "deal_type": "IPO-OFS|block deal|strategic buyout|PE secondary|PE primary|open offer|promoter sale|DRHP filing|other|unknown",
  "amount_cr": null,
  "amount_raw": "exact text the figure came from, plus your conversion, or null",
  "individuals": ["named individuals receiving money, empty if none named"],
  "buyer": "named acquirer or buyer, or null",
  "confidence": "high|medium",
  "one_line": "under 20 words: what happened and who gets paid"
}}]

Never invent a figure. If no amount is stated, amount_cr and amount_raw are
null. Do not estimate from stake percentages or valuations.

Set confidence to "high" when a named individual and a stated amount are both
present. Otherwise "medium".
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

Mark qualify = false when the item is any of these (confirmed noise):
1. IPO subscription / GMP / anchor-book / listing-day / trading-debut coverage,
   or a mere INTENTION to launch an IPO ("plans to IPO", "eyes IPO").
2. A PRIMARY fundraise — money goes INTO the company, or an individual INVESTS
   money IN; nobody cashes out. ("raises Rs X cr", "to raise funds", a funding
   round, an investor "invests Rs X cr", anchor investors).
3. A company acquiring a small or minority stake in another with no individual
   selling, or where the stake is clearly below the size bar.
4. A pure fund-to-fund or corporate-to-corporate transfer with no individual in
   the selling chain; PSU / government divestment; debt (NCDs, bonds,
   refinancing); IBC / NCLT resolution; intra-group restructuring.
5. Earnings, share-price moves, analyst ratings, product or market commentary,
   or listicles.

SCALE. If a size is stated it must convert to at least {THRESHOLD_CR} crore INR
(1 crore = 10 million; 1 USD = {USD_INR} INR, 1 EUR = {EUR_INR} INR). Drop
clearly smaller deals. If the size is undisclosed, qualify only when it is
clearly a large / control / strategic transaction. Never invent a figure.

When a LARGE deal is genuinely borderline — a big buyout where you cannot tell
if an individual sells — LEAN QUALIFY. A rare false alarm costs five seconds;
a missed founder cash-out costs a client. But never pass the numbered noise
categories above; those are confirmed drops.

"company" is ALWAYS the entity whose ownership is changing — the target, or the
company whose shares are being sold — NEVER the acquirer or investor. In
"IndiaRF acquires Fine Edge Engineering", company is Fine Edge Engineering and
buyer is IndiaRF.

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
  "one_line": "under 20 words: what happened and who gets paid"
}}]

Never invent a figure. amount_cr and amount_raw are null if no size is stated.
Set confidence to "high" only when a named individual and a stated amount are
both present; otherwise "medium".
"""
