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

import config
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
def test_old_deals_are_never_revisited():
    """A prospecting tool, not an archive. Deals outside the window must not
    appear in the queue however many of them name nobody."""
    tmp = os.path.join(tempfile.mkdtemp(), "age.db")
    db.init_db(tmp)
    conn = db._conn(tmp)
    for label, age in (("fresh", "-2 hour"), ("stale", "-40 day")):
        conn.execute(
            "INSERT INTO deals (deal_key, company, deal_type, individuals, url, created_at, updated_at) "
            f"VALUES (?, ?, 'block deal', '[]', 'u', datetime('now','{age}'), datetime('now'))",
            (label, label))
    conn.commit(); conn.close()

    got = [d["company"] for d in enrich.candidates(None, path=tmp)]
    assert "fresh" in got, got
    assert "stale" not in got, f"a 40-day-old deal entered the queue: {got}"

    # ...and the manual override is the only way to reach it.
    both = [d["company"] for d in enrich.candidates(None, path=tmp, max_age_hours=0)]
    assert "stale" in both and "fresh" in both, both


def test_window_matches_the_site_front_page():
    """The site shows the last 24h; enriching a different span would mean the
    page and the enrichment set quietly disagree."""
    assert config.ENRICH_MAX_AGE_HOURS == 24, config.ENRICH_MAX_AGE_HOURS


def test_drhp_is_ranked_last():
    """DRHP filings are the biggest bucket (175) and the worst yield (5.1%).
    Untargeted the chain named 10%; targeted it named 20%."""
    assert enrich.PRIORITY[-1] == "drhp filing", enrich.PRIORITY
    assert enrich.PRIORITY[0] == "block deal", enrich.PRIORITY
    assert enrich.PRIORITY.index("promoter sale") < enrich.PRIORITY.index("ipo-ofs")


# --------------------------------------------------------------------------
# The inline path must never be able to stop an alert.
# --------------------------------------------------------------------------
def _with_broken_enrichment(fn):
    """Run fn() with enrich_one raising on every call."""
    original = enrich.enrich_one
    enrich.enrich_one = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gemini down"))
    try:
        return fn()
    finally:
        enrich.enrich_one = original


def test_new_deal_pass_survives_a_dead_provider():
    """If Gemini is gone the run must still publish stage-2 output."""
    tmp = os.path.join(tempfile.mkdtemp(), "n.db")
    db.init_db(tmp)
    did = db.create_deal({"deal_key": "k", "company": "X", "deal_type": "block deal",
        "amount_cr": None, "amount_raw": None, "individuals": [], "seller": None,
        "buyer": None, "confidence": "medium", "one_line": "x", "source": "s",
        "url": "u", "size_source": None, "size_band": None, "confirmed": 0}, path=tmp)
    got = _with_broken_enrichment(lambda: enrich.enrich_new([did], path=tmp))
    assert got == {}, got          # nothing applied, and crucially no raise


def test_recent_pass_survives_a_dead_provider():
    tmp = os.path.join(tempfile.mkdtemp(), "b.db")
    db.init_db(tmp)
    assert _with_broken_enrichment(lambda: enrich.enrich_recent(limit=3, path=tmp)) == 0


def test_new_deal_pass_survives_a_broken_database():
    """Even a database error must not reach the alert path."""
    got = enrich.enrich_new([1, 2], path="/nonexistent/dir/nope.db")
    assert got == {}, got


def test_budgets_fit_the_free_tier():
    """13 runs/day must stay well under Gemini's ~1,000/day free quota, or the
    classifier — which runs first — starts getting 429s and alerts stop."""
    per_run = config.ENRICH_NEW_PER_RUN + config.ENRICH_RECENT_PER_RUN
    daily = 13 * (per_run + 3)      # +3 is the classifier's calls per run
    assert daily < 700, f"{daily}/day leaves too little headroom under 1,000"


def test_no_paid_fallback():
    """The system must be free, not cheap. A fallback to Anthropic costs money."""
    import llm
    assert config.CLASSIFIER_FALLBACK == "none", config.CLASSIFIER_FALLBACK
    assert llm.providers_for() == ["gemini"], llm.providers_for()


# --------------------------------------------------------------------------
# The retry state machine, exactly as specified:
#   fail once  -> "retrying" -> Telegram AND website
#   fail twice -> "failed"   -> website ONLY, no second text
# --------------------------------------------------------------------------
def _db_with_deal():
    tmp = os.path.join(tempfile.mkdtemp(), "s.db")
    db.init_db(tmp)
    did = db.create_deal({"deal_key": "k", "company": "X", "deal_type": "block deal",
        "amount_cr": None, "amount_raw": None, "individuals": [], "seller": None,
        "buyer": None, "confidence": "medium", "one_line": "x", "source": "s",
        "url": "u", "size_source": None, "size_band": None, "confirmed": 0}, path=tmp)
    return tmp, did


def test_state_is_none_before_any_attempt():
    tmp, did = _db_with_deal()
    assert db.enrichment_state(did, path=tmp) is None


def test_state_is_none_when_enrichment_succeeded():
    """Succeeding without finding a name is NOT a failure — the article was
    read and named nobody, which is a fact, not an error."""
    tmp, did = _db_with_deal()
    db.record_enrichment(did, "http://a", "ok", {"individuals": []}, [], path=tmp)
    assert db.enrichment_state(did, path=tmp) is None


def test_first_failure_says_it_will_retry():
    tmp, did = _db_with_deal()
    db.record_enrichment(did, "http://a", "fetch_failed", path=tmp)
    assert db.enrichment_state(did, path=tmp) == db.ENRICHMENT_RETRYING


def test_second_failure_is_final():
    tmp, did = _db_with_deal()
    db.record_enrichment(did, "http://a", "fetch_failed", path=tmp)
    db.record_enrichment(did, "http://a", "fetch_failed", path=tmp)
    assert db.enrichment_state(did, path=tmp) == db.ENRICHMENT_FAILED


def test_only_the_retrying_state_reaches_telegram():
    """The banker is told once that a retry is coming and never pinged again
    about the same deal. A second text is exactly what this avoids."""
    import notify
    base = {"company": "X", "deal_type": "block deal", "amount_cr": 500,
            "one_line": "x", "individuals": [], "seller": "p", "source": "ET",
            "url": "http://x", "confidence": "high", "size_source": "stated"}
    assert "will try next run" in notify.format_alert({**base, "enrichmentState": "retrying"})
    assert "Stage 3" not in notify.format_alert({**base, "enrichmentState": "failed"})
    assert "Stage 3" not in notify.format_alert({**base, "enrichmentState": None})
    assert "Stage 3" not in notify.format_alert(base)


def test_retry_pass_ignores_deals_that_merely_found_no_name():
    """Only actual failures are retried. A deal whose article was read and
    named nobody is finished, and re-fetching it would waste the quota the
    alerts depend on."""
    tmp, did = _db_with_deal()
    db.record_enrichment(did, "http://a", "ok", {"individuals": []}, [], path=tmp)
    conn = db._conn(tmp)
    rows = conn.execute(
        "SELECT d.id FROM deals d JOIN deal_enrichment e ON e.deal_id = d.id "
        "WHERE e.status != 'ok' AND e.attempts < ?", (config.ENRICH_MAX_ATTEMPTS,)
    ).fetchall()
    conn.close()
    assert rows == [], f"a successful deal entered the retry queue: {rows}"


def test_immediate_retry_is_not_a_second_recorded_attempt():
    """enrich_one retries in-process before recording. If that counted as two
    attempts, a deal would go straight to 'failed' and the promised next-run
    retry would never happen."""
    import inspect
    body = inspect.getsource(enrich.enrich_one)
    assert body.count("record_enrichment") == 2, (
        "enrich_one should record exactly once per outcome (ok / failed)")
    assert "_try_once" in body and "RETRY_PAUSE_SECONDS" in body


# --------------------------------------------------------------------------
# Synopsis — 100-150 words from the full article, shown on the deal page.
# --------------------------------------------------------------------------
def test_synopsis_fills_an_empty_field():
    words = " ".join(f"word{i}" for i in range(120))
    f = enrich._merge(deal(), {"synopsis": words})
    assert f["synopsis"].startswith("word0"), f
    assert len(f["synopsis"].split()) == 120


def test_synopsis_never_overwrites_an_existing_one():
    """Re-reading the same article can produce slightly different prose.
    Silently rewording a page the banker may already have read is worse than
    leaving it alone."""
    d = deal()
    d["synopsis"] = "Already written."
    f = enrich._merge(d, {"synopsis": " ".join(f"w{i}" for i in range(120))})
    assert "synopsis" not in f, f


def test_a_padded_headline_is_not_a_synopsis():
    """Under the floor the model is padding a headline rather than summarising
    an article, which reads worse than showing nothing."""
    f = enrich._merge(deal(), {"synopsis": "Company sells stake to buyer for money."})
    assert "synopsis" not in f, f


def test_a_long_synopsis_is_kept_whole():
    """No upper guard: an over-long synopsis is still accurate prose, and
    truncating would cut a sentence in half."""
    words = " ".join(f"word{i}" for i in range(400))
    f = enrich._merge(deal(), {"synopsis": words})
    assert len(f["synopsis"].split()) == 400, len(f["synopsis"].split())


def test_synopsis_is_asked_for_in_the_prompt_and_schema():
    assert "synopsis" in enrich.SCHEMA["properties"]
    assert "synopsis" in enrich.SCHEMA["required"]
    assert "100 to 150 words" in enrich.PROMPT


def test_stage3_no_longer_skips_deals_that_name_someone():
    """The inversion that motivated this: stage 3 used to skip named deals
    because it existed only to find names. Now it writes the synopsis too, so
    skipping them would leave the BEST deals with none."""
    import inspect
    body = inspect.getsource(enrich.enrich_new)
    assert '("[]", "", "null")' not in body, (
        "enrich_new is filtering to nameless deals again — named deals would "
        "get no synopsis")
    assert "individuals" not in inspect.getsource(enrich.candidates)


def test_synopsis_stays_out_of_the_feed_slice():
    """deals.json is 589KB and every visitor downloads it on page load.
    682 synopses would roughly double the homepage payload to carry text
    nobody sees until they open a deal."""
    import re
    src = open("export_site.py", encoding="utf-8").read()
    fields = re.search(r"FEED_FIELDS = \((.*?)\)", src, re.S).group(1)
    assert "synopsis" not in fields, "synopsis must live in the per-deal file only"
    assert '"synopsis"' in src, "but it must still be exported per deal"


# --------------------------------------------------------------------------
# Gates that can only run once the article has been read.
# --------------------------------------------------------------------------
def gated(**over):
    d = {"id": 1, "amount_cr": None, "individuals": "[]", "seller": None,
         "seller_type": None, "seller_stake_pct": None, "synopsis": None}
    d.update(over)
    return enrich.post_gate(d)


def test_a_small_deal_is_dropped_however_well_we_know_the_seller():
    """Stage 2 passes a deal as 'size undisclosed'; stage 3 finds the figure.
    Size wins over a named individual, exactly as stage 2's own Rule 8 works —
    a Rs 1cr sale is not a lead however well we know who is selling."""
    assert gated(amount_cr=1.12) is not None
    assert gated(amount_cr=155.92, individuals='["Sachidanand Upadhyay"]') is not None
    assert "below threshold" in gated(amount_cr=0.07)


def test_a_deal_at_or_above_the_floor_survives():
    assert gated(amount_cr=config.THRESHOLD_CR) is None
    assert gated(amount_cr=2500.0) is None


def test_an_unknown_amount_is_not_a_reason_to_drop():
    """Most deals never state a figure. Dropping them would empty the feed."""
    assert gated(amount_cr=None) is None


def test_a_wholly_fund_owned_sale_is_dropped():
    """Athena Renewables sold by Actis outright: a real transaction that pays
    a fund's investors, not a person."""
    r = gated(amount_cr=2500.0, seller_type="fund", seller="Actis",
              seller_stake_pct=100.0)
    assert r is not None and "wholly fund-owned" in r, r


def test_a_minority_stake_sale_by_a_fund_is_kept():
    """PE-BACKED is not PE-OWNED, and conflating them was wrong about 40 of 43
    real deals. A fund trimming a stake leaves the promoters holding the rest,
    so there is still somebody to call."""
    for pct, seller, company in ((1.0, "Capital Group", "D-Mart"),
                                 (1.6, "Peak XV Partners", "Groww"),
                                 (25.0, "Tata Capital Healthcare Fund", "Linux Labs"),
                                 (53.98, "PAG", "Nuvama Wealth")):
        assert gated(amount_cr=2000.0, seller_type="fund", seller=seller,
                     seller_stake_pct=pct) is None, f"{seller} {pct}% wrongly dropped"


def test_an_unstated_stake_keeps_the_deal():
    """25 of 43 articles never said what share changed hands. Showing one too
    many beats hiding a real one."""
    assert gated(amount_cr=900.0, seller_type="fund", seller="Blackstone",
                 seller_stake_pct=None) is None


def test_promoters_in_the_seller_string_protect_the_deal():
    """RSB Transmissions, Rs 17,600cr, sold by "RSB Transmissions
    promoters/Bain Capital" — the promoters are selling alongside the fund, so
    the promoter is the lead. `individuals` was empty because the article
    described them rather than naming them."""
    assert gated(amount_cr=17600.0, seller_type="fund", seller_stake_pct=100.0,
                 seller="RSB Transmissions promoters/Bain Capital") is None
    for s in ("the founders and Advent", "promoter family and KKR", "Mr. Shah and TPG"):
        assert gated(amount_cr=900.0, seller_type="fund", seller=s,
                     seller_stake_pct=100.0) is None, s


def test_a_named_individual_rescues_a_fund_sale():
    """If a promoter sells alongside the fund, the promoter IS the lead."""
    assert gated(amount_cr=2500.0, seller_type="fund", seller="Actis",
                 seller_stake_pct=100.0, individuals='["Ajay Piramal"]') is None


def test_other_seller_types_are_untouched():
    for t in ("individual", "family", "company", "government", "unclear", None):
        assert gated(amount_cr=900.0, seller_type=t, seller_stake_pct=100.0) is None, t


def test_the_gate_costs_no_api_call():
    """It judges facts stage 3 has already produced. If this ever starts
    calling a model it belongs somewhere else in the pipeline."""
    import inspect
    body = inspect.getsource(enrich.post_gate)
    for forbidden in ("llm.", "complete(", "extract(", "requests"):
        assert forbidden not in body, f"post_gate must not {forbidden}"


def test_seller_type_is_asked_for_in_the_schema():
    prop = enrich.SCHEMA["properties"]["seller_type"]
    assert "fund" in prop["enum"] and "individual" in prop["enum"]
    assert "seller_type" in enrich.SCHEMA["required"]
    assert "Actis" in enrich.PROMPT, "name real funds so the model has anchors"


def test_the_stake_is_asked_for_too():
    """Without it the gate cannot tell PE-backed from PE-owned."""
    assert "seller_stake_pct" in enrich.SCHEMA["properties"]
    assert "seller_stake_pct" in enrich.SCHEMA["required"]
    assert "share of the COMPANY" in enrich.PROMPT


if __name__ == "__main__":
    print("stage-3 enrichment safety tests\n")
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            check(name[5:].replace("_", " "), fn)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED"); sys.exit(1)
    print("all passed")
