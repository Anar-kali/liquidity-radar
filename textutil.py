"""
Liquidity Radar — dependency-free text helpers.

Imports nothing but `re` on purpose. Three modules need the same
Google-News-suffix and company-name logic, and they cannot share it through
any of the existing modules:

  - classify.py imports the Anthropic SDK, so anything importing it needs the
    SDK installed. export_site.py is a pure data step and must run without it.
  - filters.py imports classify AND sizing, and sizing parses 252KB of
    exchange CSVs into token indexes at import time.

So the shared piece lives here, at the bottom of the dependency graph, and
classify / filters / export_site all import from it. Before this module the
trailing-source pattern existed in two places with a comment apologising for
the duplication; export_site would have made it three.
"""
import re

# Google News appends " - Publisher" to every headline it syndicates. The
# tail must not itself contain a hyphen, so "Foo - Bar - Baz" only loses
# " - Baz".
TRAILING_SOURCE_RE = re.compile(r"\s+-\s+[^-]+$")

_WS_RE = re.compile(r"\s+")

# Length beyond which a "company name" is almost certainly a headline. The
# longest legitimate name across 601 real deals was 67 chars — "MSEDCL
# (Maharashtra State Electricity Distribution Company Limited)" — so this sits
# just above the real ceiling and only catches prose.
COMPANY_MAX_LEN = 70

# A company name never contains a currency symbol, a percentage, or
# sentence-ending punctuation — headlines routinely do. These are invariants
# rather than a blocklist of phrasings, so they do not need maintaining as
# coverage changes.
#
# An earlier draft also flagged on ". ". That was wrong: abbreviations are
# ubiquitous in Indian company names ("IFL Finance Ltd.", "Dr. Agarwal
# Healthcare", "T.C. Terrytex Limited") and it produced 7 false positives in
# 11 on real data. Kept out deliberately.
_HEADLINE_SIGNALS = ("%", "₹", "?", "!", " crore", " rs ")


def split_publisher(title):
    """
    Split a syndicated headline into (outlet, headline_without_suffix).

    Returns (None, title) when there is no suffix to strip, so the caller can
    fall back to whatever it knows about the source. Unlike
    filters.normalise_title() this preserves the original casing — the site
    displays these headlines verbatim.
    """
    if not title:
        return None, ""
    title = _WS_RE.sub(" ", str(title)).strip()
    match = TRAILING_SOURCE_RE.search(title)
    if not match:
        return None, title
    outlet = match.group(0).strip(" -").strip()
    stripped = TRAILING_SOURCE_RE.sub("", title).strip()
    # A headline that is nothing but a suffix is not a headline; keep it whole.
    if not stripped or not outlet:
        return None, title
    return outlet, stripped


def looks_like_headline(name):
    """True when a company value is prose rather than a name. Detection only."""
    if not name:
        return False
    low = f" {name.lower()} "
    return len(name) > COMPANY_MAX_LEN or any(s in low for s in _HEADLINE_SIGNALS)


def clean_company(name):
    """
    Repair the mechanically-safe defects in a model-returned company name.
    Returns (cleaned, note); note is None when nothing was wrong, else a short
    reason for the caller to log.

    Safe repairs only:
      - strip a trailing " - Publisher" Google News suffix
      - strip wrapping quotes and collapse whitespace

    Explicitly NOT repaired, because every option is a guess:
      - a headline in the company field (no way to recover the name from it)
      - two companies in one value (no way to know which the deal is about)
    Both are reported so they surface in a log instead of vanishing. Truncating
    a headline to 60 characters would produce a plausible-looking WRONG
    company, which is worse than an obviously wrong one.
    """
    if not name:
        return "", None
    cleaned = _WS_RE.sub(" ", str(name)).strip().strip('"').strip("'")

    notes = []
    stripped = TRAILING_SOURCE_RE.sub("", cleaned).strip()
    # Only accept the strip if it leaves something name-shaped behind — on a
    # short real name a stray " - " would otherwise eat half the name.
    if stripped and stripped != cleaned and len(stripped) >= 3:
        cleaned = stripped
        notes.append("publisher suffix stripped")

    if ";" in cleaned:
        notes.append("multiple companies in one value")
    if looks_like_headline(cleaned):
        notes.append("headline in company field")

    return cleaned, "; ".join(notes) if notes else None
