"""
Liquidity Radar — workflow file validity.

    python test_workflows.py

## Why this exists

On 2026-09-11 enrich.yml shipped with two `inputs:` keys in the same mapping.
That is legal YAML — a duplicate key silently keeps the LAST one — so
`yaml.safe_load` parsed it happily and the pre-push check passed. GitHub does
not accept it: the run failed the instant it was created, with no jobs and no
log, and `gh workflow run -f max_age_hours=...` returned

    HTTP 422: Unexpected inputs provided: ["max_age_hours"]

because only the second `inputs:` block existed as far as GitHub was
concerned. A plain safe_load cannot catch this class of bug, so the loader
below rejects duplicates explicitly.

The other checks are for mistakes that also fail only in CI, minutes later:
a workflow that runs the model without the key it needs, or one that quietly
reintroduces the paid provider.
"""
import sys

import yaml

WORKFLOWS = ("radar", "blockdeals", "enrich", "deploy-site", "digest",
             "feedback-report", "refresh-tickers")
# Workflows that reach llm.py and therefore need Gemini credentials.
NEEDS_MODEL = {"radar", "blockdeals", "enrich"}

FAILURES = []


class StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys."""


def _no_duplicates(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise ValueError(
                f"duplicate key {key!r} on line {key_node.start_mark.line + 1} — "
                f"YAML keeps only the last one and GitHub rejects the file")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)


def load(name):
    with open(f".github/workflows/{name}.yml", encoding="utf-8") as fh:
        return yaml.load(fh, Loader=StrictLoader) or {}


def check(name, fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(name)
        print(f"  FAIL  {name}\n        {exc}")
    else:
        print(f"  ok    {name}")


def test_no_duplicate_keys():
    for name in WORKFLOWS:
        load(name)          # StrictLoader raises on a duplicate


def test_env_is_a_mapping_of_strings():
    """A stray list or nested mapping under env is accepted locally and
    rejected by GitHub."""
    for name in WORKFLOWS:
        doc = load(name)
        for job in (doc.get("jobs") or {}).values():
            for step in (job.get("steps") or []):
                for key, val in (step.get("env") or {}).items():
                    assert isinstance(val, (str, int, bool)), \
                        f"{name}: env {key} is {type(val).__name__}"


def test_model_workflows_carry_the_gemini_key():
    """A workflow that reaches llm.py without GEMINI_API_KEY runs the whole
    pipeline and fails at the first model call, minutes in."""
    for name in NEEDS_MODEL:
        src = open(f".github/workflows/{name}.yml", encoding="utf-8").read()
        assert "GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}" in src, \
            f"{name}.yml reaches the model but never passes GEMINI_API_KEY"


def test_no_workflow_passes_the_paid_key():
    """The system must be free, not cheap. Passing ANTHROPIC_API_KEY makes the
    paid provider reachable again; a comment mentioning it is fine."""
    for name in WORKFLOWS:
        for i, line in enumerate(open(f".github/workflows/{name}.yml", encoding="utf-8"), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert not stripped.startswith("ANTHROPIC_API_KEY:"), \
                f"{name}.yml line {i} passes the paid key"


def test_deploy_checks_out_main():
    """A bare checkout takes github.sha — for a workflow_call that is the SHA
    from before the caller committed its new site data, so the deploy ships
    the PREVIOUS run's export and the site sits one deploy behind forever."""
    doc = load("deploy-site")
    steps = [s for j in (doc.get("jobs") or {}).values() for s in (j.get("steps") or [])]
    checkouts = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout")]
    assert checkouts, "deploy-site has no checkout step"
    for step in checkouts:
        assert (step.get("with") or {}).get("ref") == "main", (
            "deploy-site must check out ref: main, or it publishes the commit "
            "from before the pipeline wrote its data")


def test_enrich_exposes_both_dispatch_inputs():
    """The exact failure that motivated this file."""
    doc = load("enrich")
    on = doc.get(True, doc.get("on")) or {}       # bare `on:` parses as True
    inputs = (on.get("workflow_dispatch") or {}).get("inputs") or {}
    assert "limit" in inputs and "max_age_hours" in inputs, sorted(inputs)


if __name__ == "__main__":
    print("workflow file checks\n")
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            check(_name[5:].replace("_", " "), _fn)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED")
        sys.exit(1)
    print("all passed")
