"""
Liquidity Radar — stage 3 safety tests.

    python test_enrich.py

Enrichment WRITES ONTO EXISTING DEALS, which makes it the most dangerous code
in the pipeline: a mistake here does not drop a lead, it corrupts a record the
banker is already acting on. These cover the three ways that can happen.
"""
import json
import os
import sqlite3
import sys
import tempfile

import db
import enrich

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


def deal(**over):
    base = {"id": 1, "company": "X Ltd", "deal_type": "block deal", "url": "http://x",
            "individuals": "[]", "amount_cr": None, "amount_raw": None,
            "seller": None, "buyer": None, "one_line": "something"}
    base.update(over)
    return base


# --------------------------------------------------------------------------
# _merge must only ever FILL A HOLE.
# --------------------------------------------------------------------------
def test_never_overwrites_an_existing_amount():
    """A stated amount reached the deal through the alert path and may be
    exchange-confirmed. An enriched one is a model reading prose."""
    f = enrich._merge(deal(amount_cr=500.0), {"amount_cr": 900.0})
    assert "amount_cr" not in f, f
    assert "size_source" not in f, f


def test_never_overwrites_existing_individuals():
    f = enrich._merge(deal(individuals='["Real Person"]'), {"individuals": ["Someone Else"]})
    assert "individuals" not in f, f


def test_never_overwrites_an_existing_seller():
    f = enrich._merge(deal(seller="Promoter A"), {"seller": "Someone Else"})
    assert "seller" not in f, f


def test_fills_an_empty_amount_and_marks_provenance():
    f = enrich._merge(deal(), {"amount_cr": 900.0, "amount_raw": "Rs 900 crore"})
    assert f["amount_cr"] == 900.0, f
    assert f["size_source"] == "stated", f
    assert "Rs 900 crore" in f["amount_raw"], f


def test_records_the_quoted_phrase_not_a_label():
    """The site renders amount_raw beside the figure so a reader can check it."""
    f = enrich._merge(deal(), {"amount_cr": 12.0, "amount_raw": "about Rs 12 crore"})
    assert "about Rs 12 crore" in f["amount_raw"], f


# --------------------------------------------------------------------------
# Names reaching the deal record must be people.
# --------------------------------------------------------------------------
def test_company_names_are_not_people():
    got = enrich._clean_names([
        "Kotak Mahindra Capital", "Blackstone Group", "SoftBank Vision Fund",
        "ABC Ventures Pvt Ltd", "XYZ Holdings", "State Bank", "Some Trust"])
    assert got == [], got


def test_single_tokens_are_dropped():
    assert enrich._clean_names(["Ramesh", "Infosys"]) == []


def test_real_names_survive():
    got = enrich._clean_names(["Girish Kapoor", " Manoj   Agrawal ", "Vijay Shekhar Sharma"])
    assert got == ["Girish Kapoor", "Manoj Agrawal", "Vijay Shekhar Sharma"], got


def test_multi_party_field_is_trimmed_at_a_boundary():
    long = ", ".join(f"Investor Number {i}" for i in range(20))
    f = enrich._merge(deal(), {"buyer": long})
    assert len(f["buyer"]) <= 130, len(f["buyer"])
    assert f["buyer"].endswith("+others"), f["buyer"]
    assert not f["buyer"].rstrip(" +others").endswith(","), "trimmed mid-name"


# --------------------------------------------------------------------------
# Writes must land in the database the caller named.
# --------------------------------------------------------------------------
def test_writes_go_to_the_given_path_not_the_module_default():
    """Regression: db helpers bind `path=DB_PATH` AT IMPORT TIME, so setting
    db.DB_PATH afterwards does nothing and every write lands in radar.db. A
    --db test run did exactly that to the real database."""
    tmp = os.path.join(tempfile.mkdtemp(), "side.db")
    db.init_db(tmp)
    db.record_enrichment(42, "http://a", "ok", {"individuals": ["A B"]}, ["individuals"], path=tmp)
    db.gnews_cache("TOKEN", "http://a", path=tmp)

    side = sqlite3.connect(tmp)
    assert side.execute("SELECT COUNT(*) FROM deal_enrichment").fetchone()[0] == 1
    assert side.execute("SELECT COUNT(*) FROM gnews_cache").fetchone()[0] == 1

    # ...and the real database must be untouched by that call.
    if os.path.exists(db.DB_PATH):
        real = sqlite3.connect(db.DB_PATH)
        row = real.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='deal_enrichment'"
        ).fetchone()[0]
        if row:
            n = real.execute("SELECT COUNT(*) FROM deal_enrichment WHERE deal_id = 42").fetchone()[0]
            assert n == 0, "write leaked into the real radar.db"


def test_cached_failure_is_distinct_from_never_tried():
    """Without this distinction every run retries the same dead tokens."""
    tmp = os.path.join(tempfile.mkdtemp(), "c.db")
    db.init_db(tmp)
    db.gnews_cache("DEAD", "", path=tmp)
    assert db.gnews_cached("DEAD", path=tmp) == "", "cached failure lost"
    assert db.gnews_cached("UNSEEN", path=tmp) is None, "never-tried must be None"


# --------------------------------------------------------------------------
# Queue ordering is the whole design — DRHP last.
# --------------------------------------------------------------------------
def test_drhp_is_ranked_last():
    """DRHP filings are the biggest bucket (175) and the worst yield (5.1%).
    Untargeted the chain named 10%; targeted it named 20%."""
    assert enrich.PRIORITY[-1] == "drhp filing", enrich.PRIORITY
    assert enrich.PRIORITY[0] == "block deal", enrich.PRIORITY
    assert enrich.PRIORITY.index("promoter sale") < enrich.PRIORITY.index("ipo-ofs")


if __name__ == "__main__":
    print("stage-3 enrichment safety tests\n")
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            check(name[5:].replace("_", " "), fn)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED"); sys.exit(1)
    print("all passed")
