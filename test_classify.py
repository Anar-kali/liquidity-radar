"""
Liquidity Radar — regression tests for the classifier's batch contract.

    python test_classify.py

No pytest: requirements.txt is what the GitHub Actions runner installs on every
run, and this does not need to be in it. Plain asserts, one process, exit 1 on
failure.

## What is actually being protected here

Results are aligned to the batch BY POSITION. classify_batch and
precision_batch both do `results[i] if i < len(results) else {}`, and
_normalise2 reads a missing dict as qualify=False. So before the length check
existed, a model returning 7 objects for an 8-item batch silently dropped the
8th item as a non-deal — no exception, no log line, and the item was marked
seen so it never came back. That is a lead the banker never hears about.

_parse_array took an `expected` argument for months and never looked at it.
These tests exist so it cannot quietly stop looking at it again.
"""
import sys

import classify

FAILURES = []


def check(name, fn):
    try:
        fn()
    except AssertionError as exc:
        FAILURES.append(f"{name}: {exc}")
        print(f"  FAIL  {name}\n        {exc}")
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(f"{name}: unexpected {type(exc).__name__}: {exc}")
        print(f"  ERROR {name}\n        {type(exc).__name__}: {exc}")
    else:
        print(f"  ok    {name}")


def raises(fn, needle=""):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        assert needle in str(exc), f"expected {needle!r} in {str(exc)!r}"
        return
    raise AssertionError("expected an exception, none raised")


# --------------------------------------------------------------------------
# _parse_array — the length contract
# --------------------------------------------------------------------------
def test_short_array_raises():
    """7 results for 8 items must raise, not truncate."""
    seven = "[" + ",".join('{"n":%d,"qualify":false}' % i for i in range(1, 8)) + "]"
    raises(lambda: classify._parse_array(seven, 8),
           "returned 7 results for 8 items")


def test_long_array_raises():
    """9 for 8 is just as wrong — the extra shifts nothing but means the model
    lost track of the batch, so the whole response is untrustworthy."""
    nine = "[" + ",".join('{"n":%d}' % i for i in range(1, 10)) + "]"
    raises(lambda: classify._parse_array(nine, 8),
           "returned 9 results for 8 items")


def test_exact_length_passes():
    three = '[{"n":1,"neg":true,"r":9},{"n":2,"neg":false,"r":null},{"n":3,"neg":false,"r":null}]'
    out = classify._parse_array(three, 3)
    assert len(out) == 3, out
    assert out[0]["r"] == 9, out[0]


def test_markdown_fence_still_tolerated():
    """Fence-stripping predates the length check and must survive it."""
    fenced = '```json\n[{"n":1,"neg":true,"r":9}]\n```'
    out = classify._parse_array(fenced, 1)
    assert len(out) == 1 and out[0]["neg"] is True, out


def test_empty_array_for_nonempty_batch_raises():
    raises(lambda: classify._parse_array("[]", 5), "returned 0 results for 5 items")


def test_bare_object_raises():
    """No array at all — caught by the bracket scan before the length check."""
    raises(lambda: classify._parse_array('{"n":1}', 1), "no JSON array")


def test_json_scalar_raises():
    """Brackets present but the payload is not a list of results."""
    raises(lambda: classify._parse_array('[[1,2]]', 2), "returned 1 results for 2 items")


# --------------------------------------------------------------------------
# The end-to-end path: a short response must reach the caller as a FAILED
# batch, so main.py parks the items, rather than as quiet verdicts.
# --------------------------------------------------------------------------
def _with_stub_response(text, fn):
    """Run fn() with llm.complete replaced by one that returns `text`."""
    import llm
    original = llm.complete
    llm.complete = lambda **kw: text
    try:
        return fn()
    finally:
        llm.complete = original


def test_stage2_short_response_does_not_drop_item_8():
    seven = "[" + ",".join(
        '{"n":%d,"qualify":false,"company":"X","deal_type":"other",'
        '"amount_cr":null,"individuals":[],"confidence":"medium",'
        '"one_line":"x","size_band":"UNKNOWN"}' % i for i in range(1, 8)) + "]"
    batch = [{"title": f"headline {i}", "description": ""} for i in range(8)]
    raises(lambda: _with_stub_response(seven, lambda: classify.precision_batch(batch)),
           "returned 7 results for 8 items")


def test_stage1_short_response_raises_too():
    four = "[" + ",".join('{"n":%d,"neg":true,"r":9}' % i for i in range(1, 5)) + "]"
    batch = [{"title": f"headline {i}", "description": ""} for i in range(5)]
    raises(lambda: _with_stub_response(four, lambda: classify.classify_batch(batch)),
           "returned 4 results for 5 items")


def test_classify_all_parks_the_batch_rather_than_verdicting_it():
    """The failure must arrive as classify_failed, which main.py parks. If this
    ever comes back as a real verdict, unclassified items reach Telegram —
    that is the 2026-08-21 incident."""
    four = "[" + ",".join('{"n":%d,"neg":true,"r":9}' % i for i in range(1, 5)) + "]"
    items = [{"title": f"headline {i}", "description": ""} for i in range(5)]
    out = _with_stub_response(four, lambda: classify.classify_all(items))
    assert len(out) == 5, f"expected 5 (item, result) pairs, got {len(out)}"
    for _, result in out:
        assert result["classify_failed"] is True, result
        assert result["confirmed_negative"] is False, result


# --------------------------------------------------------------------------
# Schemas must stay in step with what the prompts declare and with _normalise.
# --------------------------------------------------------------------------
def test_schema_pins_array_length_to_batch():
    for build in (classify._stage1_schema, classify._stage2_schema, classify._seller_schema):
        s = build(11)
        assert s["minItems"] == 11 and s["maxItems"] == 11, (build.__name__, s)


def test_stage2_schema_matches_normalise_keys():
    """Every key _normalise2 reads must be offered by the schema, or a
    schema-constrained model can never produce it."""
    props = set(classify._stage2_schema(1)["items"]["properties"])
    for key in ("qualify", "drop_reason", "company", "deal_type", "amount_cr",
                "amount_raw", "individuals", "seller", "buyer", "confidence",
                "one_line", "size_band", "size_basis"):
        assert key in props, f"_normalise2 reads {key!r}, schema does not offer it"


def test_stage2_enums_cover_normalise_defaults():
    """_normalise2 falls back to 'unknown'/'UNKNOWN'/'medium'; a constrained
    model must be allowed to emit those exact values."""
    props = classify._stage2_schema(1)["items"]["properties"]
    assert "unknown" in props["deal_type"]["enum"]
    assert "UNKNOWN" in props["size_band"]["enum"]
    assert "medium" in props["confidence"]["enum"]


# --------------------------------------------------------------------------
# Shadow mode must be incapable of affecting a live run.
# --------------------------------------------------------------------------
def _shadow_run(shadow_text, record=None):
    """Run stage 1 with a shadow provider that returns `shadow_text`.

    `shadow_text` may be an Exception, in which case the shadow call raises.
    Returns the primary's aligned verdicts.
    """
    import config, db, llm
    good = '[{"n":1,"neg":false,"r":null},{"n":2,"neg":false,"r":null}]'
    calls = {"n": 0}

    def fake_complete(**kw):
        calls["n"] += 1
        if kw.get("provider"):                 # the shadow leg
            if isinstance(shadow_text, Exception):
                raise shadow_text
            return shadow_text
        return good                            # the primary leg

    original_complete, original_add = llm.complete, db.add_classifier_shadow
    original_shadow = config.CLASSIFIER_SHADOW
    llm.complete = fake_complete
    db.add_classifier_shadow = (lambda **kw: record.append(kw)) if record is not None \
        else (lambda **kw: None)
    config.CLASSIFIER_SHADOW = "gemini"
    try:
        batch = [{"title": "a", "description": "", "url": "u1"},
                 {"title": "b", "description": "", "url": "u2"}]
        return classify.classify_batch(batch), calls["n"]
    finally:
        llm.complete, db.add_classifier_shadow = original_complete, original_add
        config.CLASSIFIER_SHADOW = original_shadow


def test_shadow_exception_does_not_break_the_primary():
    aligned, calls = _shadow_run(RuntimeError("gemini is down"))
    assert calls == 2, f"expected primary + shadow call, got {calls}"
    assert len(aligned) == 2, aligned
    for _, r in aligned:
        assert r["confirmed_negative"] is False, r
        assert r["classify_failed"] is False, r


def test_shadow_short_array_does_not_break_the_primary():
    """The shadow leg parses through the same length check; when it trips,
    the primary's verdicts must still come back intact."""
    aligned, _ = _shadow_run('[{"n":1,"neg":true,"r":9}]')
    assert len(aligned) == 2 and all(not r["confirmed_negative"] for _, r in aligned), aligned


def test_shadow_records_both_verdicts_and_agreement():
    rows = []
    _shadow_run('[{"n":1,"neg":true,"r":9},{"n":2,"neg":false,"r":null}]', record=rows)
    assert len(rows) == 2, rows
    # primary said pass on both; shadow rejected the first.
    assert rows[0]["primary_reject"] is False and rows[0]["shadow_reject"] is True, rows[0]
    assert rows[1]["primary_reject"] == rows[1]["shadow_reject"] is False, rows[1]
    assert rows[0]["shadow_detail"] == 9, rows[0]


def test_shadow_is_off_by_default():
    """Nothing should call the shadow leg unless CLASSIFIER_SHADOW is set."""
    import config, llm
    assert config.CLASSIFIER_SHADOW == "", (
        f"CLASSIFIER_SHADOW defaults to {config.CLASSIFIER_SHADOW!r}; it must be "
        "off unless explicitly enabled")
    original = llm.complete
    calls = {"n": 0}

    def fake(**kw):
        calls["n"] += 1
        return '[{"n":1,"neg":false,"r":null}]'
    llm.complete = fake
    try:
        classify.classify_batch([{"title": "a", "description": "", "url": "u"}])
    finally:
        llm.complete = original
    assert calls["n"] == 1, f"shadow ran when disabled ({calls['n']} calls)"


if __name__ == "__main__":
    print("classifier batch-contract tests\n")
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            check(name[5:].replace("_", " "), fn)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED")
        sys.exit(1)
    print("all passed")
