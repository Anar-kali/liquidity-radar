"""
Liquidity Radar — the provider seam.

Everything that knows an LLM vendor exists lives in this file. classify.py
hands over a system prompt, a user message and a JSON schema, and gets back a
string of JSON. It never imports a vendor SDK, never sees a client object, and
does not change when the provider does.

## Why the seam exists at all

Stages 1 and 2 are ~63 model calls/day between them (measured over 788 runs:
39.4 stage-1 + 24.0 stage-2, peaking at 144 on the busiest day). That is $9.49
a month on Haiku, so the move to Gemini's free tier is worth about nine dollars
— not enough on its own to justify touching a pipeline that alerts on money.

What makes it worth doing is that a second provider ends a single point of
failure. On 2026-08-21 anthropic 1.0.0 removed the `temperature` argument,
every batch raised, and 80 raw headlines went to Telegram. `classify_retry` now
catches that class of failure, but a pipeline with exactly one way to reach a
model still stops working when that one way breaks. Here, a provider outage
degrades to the other provider instead.

## Schemas are enforced on Gemini and advisory on Anthropic

Gemini constrains decoding to `response_schema`, so the SHAPE of each object is
guaranteed there. Length is not, everywhere: Gemini rejects minItems/maxItems on
stage 2's 14-field object with an opaque 400, so that schema ships without a
length pin (see classify's schema section). The pinned anthropic SDK (0.125.0)
predates structured outputs entirely, so there the whole schema is documentation.

classify._parse_array's length check is therefore the only guarantee that holds
on every provider and every stage. It is the backstop, not a workaround for one
vendor. Do not remove it on the grounds that the schema already covers it —
on the stage where a wrong length silently drops a lead, it does not.

## Fallback

A failed call retries on the same provider (free tiers rate-limit, and a 429 is
usually over in seconds), then falls back to the other provider before giving
up. Only when both fail does the exception reach classify, which parks the item
in classify_retry rather than alerting it unclassified.
"""
import os
import random
import threading
import time

import config

# Wall-clock of the last request per provider, so pacing survives across
# batches within one run. A run is single-threaded today; the lock costs
# nothing and means this stays correct if that ever changes.
_last_call = {}
_last_call_lock = threading.Lock()

# Per-run accounting, so a run can say which provider actually served it.
# This exists because a fallback is INVISIBLE otherwise: if GEMINI_API_KEY is
# missing or the free tier throttles, every batch quietly completes on
# Anthropic and the run looks identical to a healthy one — same alerts, same
# funnel, same exit code, and a bill nobody is watching.
USAGE = {}

# Clients are built once per process. Constructing an Anthropic() per batch was
# deliberate once (v4 Change 6, so one unreachable API didn't abort a whole
# run) but that is now handled by the try/except in classify_all — the client
# itself is cheap to keep.
_clients = {}


class AllProvidersFailed(Exception):
    """Every configured provider refused. Carries the last error from each."""

    def __init__(self, errors):
        self.errors = errors
        detail = "; ".join(f"{p}: {type(e).__name__}: {e}" for p, e in errors.items())
        super().__init__(f"all providers failed — {detail}")


def _pace(provider):
    """Sleep just enough to stay under the provider's requests-per-minute cap.

    Gemini's free tier allows 15 RPM and the worst single run we have measured
    issues 19 calls back to back, so without this a busy run trips the limit
    partway through and the tail of the batch lands in classify_retry.
    """
    rpm = config.PROVIDER_RPM.get(provider)
    if not rpm:
        return
    gap = 60.0 / rpm
    with _last_call_lock:
        previous = _last_call.get(provider)
        now = time.monotonic()
        if previous is not None:
            wait = gap - (now - previous)
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
        _last_call[provider] = now


def _anthropic(model, system, user, max_tokens, schema):
    """Unchanged from the original call site. `schema` is advisory here — see
    the module docstring."""
    import anthropic

    client = _clients.get("anthropic")
    if client is None:
        # Reads ANTHROPIC_API_KEY from the environment. Never hardcode the key.
        client = _clients["anthropic"] = anthropic.Anthropic()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,  # deterministic — this is classification, not writing
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def _gemini(model, system, user, max_tokens, schema):
    from google import genai
    from google.genai import types

    client = _clients.get("gemini")
    if client is None:
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        client = _clients["gemini"] = genai.Client(api_key=key)

    cfg = types.GenerateContentConfig(
        system_instruction=system,
        temperature=0,
        max_output_tokens=max_tokens,
        response_mime_type="application/json",
        response_schema=schema,
    )
    resp = client.models.generate_content(model=model, contents=user, config=cfg)
    text = resp.text
    if not text:
        # A schema-constrained call that returns nothing has usually hit the
        # output cap or a safety block; both are worth seeing in the log rather
        # than surfacing later as an empty-array parse error.
        reason = getattr(resp.candidates[0], "finish_reason", None) if resp.candidates else None
        raise ValueError(f"gemini returned no text (finish_reason={reason})")
    return text


_BACKENDS = {"anthropic": _anthropic, "gemini": _gemini}


def _attempt(provider, stage, system, user, max_tokens, schema):
    """One provider, with retries. Raises if every retry is exhausted."""
    model = config.PROVIDER_MODELS[provider][stage]
    backend = _BACKENDS[provider]
    last = None
    for attempt in range(config.LLM_RETRY_ATTEMPTS):
        try:
            _pace(provider)
            out = backend(model, system, user, max_tokens, schema)
            USAGE.setdefault(provider, {"calls": 0, "failures": 0})["calls"] += 1
            return out
        except Exception as exc:  # noqa: BLE001 — vendor SDKs raise unrelated types
            USAGE.setdefault(provider, {"calls": 0, "failures": 0})["failures"] += 1
            last = exc
            if attempt == config.LLM_RETRY_ATTEMPTS - 1:
                break
            # Full jitter. Several batches that hit the same rate limit would
            # otherwise retry in lockstep and trip it again together.
            delay = min(config.LLM_RETRY_MAX_SLEEP, 2 ** attempt) * (0.5 + random.random())
            print(f"[llm] {provider}/{model} attempt {attempt + 1} failed "
                  f"({type(exc).__name__}: {str(exc)[:120]}), retrying in {delay:.1f}s")
            time.sleep(delay)
    raise last


def summary():
    """One line naming who actually served this run, or None if no calls were
    made. Print it: it is the only signal separating "running on the
    configured provider" from "silently running on the fallback"."""
    if not USAGE:
        return None
    configured = config.CLASSIFIER_PROVIDER
    parts = [f"{name} {u['calls']} ok"
             + (f" / {u['failures']} failed" if u["failures"] else "")
             for name, u in sorted(USAGE.items())]
    line = "[llm] " + ", ".join(parts)
    served = [n for n, u in USAGE.items() if u["calls"]]
    if served and configured not in served:
        line += (f"   ** {configured.upper()} SERVED NOTHING — this run ran "
                 f"entirely on the fallback **")
    elif len(served) > 1:
        line += "   ** fell back mid-run **"
    return line


def providers_for(provider=None):
    """The provider to try first, then whatever backs it up.

    An unset CLASSIFIER_FALLBACK resolves to the other configured provider
    rather than to a hardcoded name, so this is right both before and after
    cutover. With more than two providers the choice stops being obvious and
    an explicit CLASSIFIER_FALLBACK is required.
    """
    primary = provider or config.CLASSIFIER_PROVIDER
    order = [primary]
    fallback = (config.CLASSIFIER_FALLBACK or "").strip().lower()
    if fallback == "none":
        return order
    if not fallback:
        others = [p for p in config.PROVIDER_MODELS if p != primary]
        fallback = others[0] if len(others) == 1 else ""
    if fallback and fallback != primary:
        order.append(fallback)
    return order


def complete(system, user, max_tokens, schema=None, stage=1, provider=None,
             allow_fallback=True):
    """Send one prompt, return the model's raw text.

    Tries the configured provider, then the fallback. Raises
    AllProvidersFailed only when nothing worked — classify treats that as a
    failed batch and parks the items, so an unreachable model never becomes an
    unclassified alert.
    """
    order = providers_for(provider)
    if not allow_fallback:
        # A shadow run that quietly completes on the primary would be recorded
        # under the shadow provider's name — the most misleading row this
        # system could write. Same reasoning in eval_classifier.
        order = order[:1]
    errors = {}
    for name in order:
        try:
            return _attempt(name, stage, system, user, max_tokens, schema)
        except Exception as exc:  # noqa: BLE001
            errors[name] = exc
            print(f"[llm] provider {name} exhausted for stage {stage}: "
                  f"{type(exc).__name__}: {str(exc)[:160]}")
    raise AllProvidersFailed(errors)
