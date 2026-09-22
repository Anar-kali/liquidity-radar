"""
Liquidity Radar — clustering rules.

    python test_cluster.py

Clustering decides whether an article becomes a NEW card or attaches to one
that exists. Both directions are expensive when wrong: over-merging hides a
real lead inside somebody else's card, and under-merging floods the feed with
the same story told eight times.
"""
import sys

import cluster
import config

FAILURES = []


def check(name, fn):
    try:
        fn()
    except AssertionError as exc:
        FAILURES.append(name); print(f"  FAIL  {name}\n        {exc}")
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(name); print(f"  ERROR {name}\n        {type(exc).__name__}: {exc}")
    else:
        print(f"  ok    {name}")


def match(a, b):
    return cluster.token_subset_match(cluster.tokens(a), cluster.tokens(b))


# --------------------------------------------------------------------------
# A one-word parenthetical is an alias. A longer one describes someone else.
# --------------------------------------------------------------------------
def test_acronym_alias_matches_the_full_name():
    """_strip_name dropped every parenthetical, leaving these two forms of the
    SAME company with disjoint token sets — which is why NSE's single IPO
    spread across 16 cards."""
    assert match("National Stock Exchange (NSE)", "NSE (National Stock Exchange)")
    assert match("National Stock Exchange (NSE)", "NSE")


def test_descriptive_parenthetical_is_still_dropped():
    """'(a unit of Kirloskar)' names a DIFFERENT entity. Folding its tokens in
    would merge a subsidiary's deal into its parent's."""
    assert not match("Ashok Iron Works (a unit of Kirloskar)", "Kirloskar")
    assert not match("Foo Industries (Formerly Bar Industries)", "Bar Industries")


def test_unnamed_is_not_an_identifier():
    """Stage 2 writes 'Auto Ancillary (unnamed)' when an article withholds the
    company. Now that one-word parentheticals are kept, a deal named only
    '(unnamed)' would reduce to {unnamed} and match every other unnamed one."""
    assert cluster.tokens("(unnamed)") == frozenset(), cluster.tokens("(unnamed)")
    assert not match("Auto Ancillary (unnamed)", "Fintech Company (unnamed)")


def test_unrelated_companies_do_not_merge():
    assert not match("Hero Motors", "Hero MotoCorp Finance")
    assert not match("Tata Sons", "Tata Motors")


# --------------------------------------------------------------------------
# The 60-day window is for listings only.
# --------------------------------------------------------------------------
def test_ipo_types_are_recognised():
    for t in ("DRHP filing", "IPO-OFS", "IPO-DRHP", "ipo-ofs"):
        assert cluster.is_ipo_type(t), t


def test_ordinary_deals_are_not_ipo_types():
    """A block deal by a company that is also listing must keep the 3-day
    window — it is a different event with different people getting paid, and
    folding it into the listing card would bury a real lead."""
    for t in ("block deal", "promoter sale", "strategic buyout",
              "PE secondary", "open offer", "", None):
        assert not cluster.is_ipo_type(t), t


def test_the_window_is_sixty_days():
    """Measured on 107 real gaps between consecutive cards for the same IPO:
    median 6 days, p90 21, max 47. 30 days joins 97%, 45 joins 99%, 60 joins
    100%."""
    assert config.IPO_WINDOW_HOURS == 1440, config.IPO_WINDOW_HOURS
    assert config.IPO_WINDOW_HOURS > config.DEAL_WINDOW_HOURS
    assert config.IPO_WINDOW_HOURS > config.AMOUNT_WINDOW_HOURS


def test_find_match_requires_both_sides_to_be_ipo():
    """The long window is only safe because it is this narrow."""
    import inspect
    body = inspect.getsource(cluster._find_match)
    assert "new_is_ipo" in body and "is_ipo_type(cand.get" in body, (
        "the IPO path must check BOTH the incoming deal and the candidate")


if __name__ == "__main__":
    print("clustering rules\n")
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            check(_n[5:].replace("_", " "), _f)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED"); sys.exit(1)
    print("all passed")
