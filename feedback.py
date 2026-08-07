"""
Liquidity Radar — Telegram feedback buttons (polling, no webhook).

Every alert carries an inline keyboard: Useful / Already knew / Noise.
Button presses arrive as `callback_query` updates. There is no persistent
server here, so we poll `getUpdates` at the start of every run instead of
using a webhook — feedback landing within ~15 minutes is fine, nothing here
needs instant acknowledgement.

NOTE: getUpdates and webhooks are mutually exclusive on a bot. If a webhook
is ever set on @Deal_trackbot, delete it first (setWebhook with an empty url)
or polling will silently stop receiving updates.

This module only records feedback and reports it (see feedback_report.py). It
never modifies prompts, thresholds, or rules automatically.
"""

import re

import requests

import db
import notify

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

VERDICT_SUFFIX = {
    "useful": "\U0001F44D Rated: Useful",
    "already_knew": "\U0001F937 Rated: Already knew",
    "noise": "\U0001F5D1 Rated: Noise",
}

_CALLBACK_RE = re.compile(r"^fb:(\d+):(useful|already_knew|noise)$")


def _call(token, method, payload):
    try:
        r = requests.post(TELEGRAM_API.format(token=token, method=method),
                           json=payload, timeout=30)
        return r.json() if r.status_code == 200 else None
    except Exception as exc:  # noqa: BLE001
        print(f"[feedback] {method} failed: {exc}")
        return None


def _edit_with_verdict(token, chat_id, message_id, original_text, original_entities, verdict):
    """
    Append the verdict to the message without re-parsing Markdown (round-
    tripping already-rendered text back through parse_mode can break on
    escaped characters). Passing the original `entities` back verbatim keeps
    the existing formatting; the appended suffix is plain text after them.
    """
    new_text = f"{original_text}\n\n{VERDICT_SUFFIX[verdict]}"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": new_text,
        "reply_markup": {"inline_keyboard": []},  # remove the buttons once rated
    }
    if original_entities:
        payload["entities"] = original_entities
    _call(token, "editMessageText", payload)


def poll_feedback(dry=False):
    """
    Pull any pending button presses since the last run, log them, acknowledge
    them, and edit the original message to show the verdict.

    In --dry mode, updates are inspected and printed but neither written nor
    acknowledged, so they remain pending and get processed for real on the
    next live run.
    """
    token, chat_id_env = notify._token(), notify._chat_id()
    if not token or not chat_id_env:
        return

    offset = int(db.get_state("telegram_offset", "0"))
    resp = _call(token, "getUpdates", {
        "offset": offset,
        "allowed_updates": ["callback_query"],
        "timeout": 0,
    })
    if not resp or not resp.get("ok"):
        return

    updates = resp.get("result", [])
    if not updates:
        return

    max_update_id = offset - 1
    logged = 0
    for u in updates:
        max_update_id = max(max_update_id, u["update_id"])
        cq = u.get("callback_query")
        if not cq:
            continue

        data = cq.get("data", "")
        m = _CALLBACK_RE.match(data)
        msg = cq.get("message", {}) or {}
        chat = msg.get("chat", {}).get("id")
        message_id = msg.get("message_id")
        cq_id = cq.get("id")

        if not m:
            print(f"[feedback] malformed callback_data: {data!r}")
            if not dry and cq_id:
                _call(token, "answerCallbackQuery",
                      {"callback_query_id": cq_id, "text": "Error"})
            continue

        deal_id, verdict = int(m.group(1)), m.group(2)

        if dry:
            print(f"[feedback] (dry) would log deal {deal_id} = {verdict}")
            continue

        db.add_feedback(deal_id, verdict, str(chat))
        logged += 1

        if cq_id:
            _call(token, "answerCallbackQuery",
                  {"callback_query_id": cq_id, "text": "Logged"})

        if chat and message_id:
            _edit_with_verdict(token, chat, message_id, msg.get("text", ""),
                               msg.get("entities"), verdict)

    if not dry:
        db.set_state("telegram_offset", str(max_update_id + 1))
    print(f"[feedback] {'(dry) ' if dry else ''}{logged} verdict(s) logged, "
          f"{len(updates)} update(s) seen")
