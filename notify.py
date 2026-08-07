"""
Liquidity Radar — Telegram alerts.

Alert format (Markdown), scannable, ~8-15 a day:

    [RED] *Company* · deal_type · amount or "Size undisclosed"

    _one_line_

    names if any, else "No individual named"

    [source](url)

A red circle emoji = high confidence, yellow = medium. Follow-ups on an
existing deal are prefixed "UPDATE ·".
"""

import os

import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _token():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _chat_id():
    return os.environ.get("TELEGRAM_CHAT_ID", "")


def _escape(text):
    """Escape characters that break Telegram's legacy Markdown."""
    if text is None:
        return ""
    for ch in ("_", "*", "[", "]", "`"):
        text = text.replace(ch, f"\\{ch}")
    return text


def format_amount(amount_cr):
    if amount_cr is None:
        return "Size undisclosed"
    # Render whole numbers cleanly, keep one decimal otherwise.
    if float(amount_cr).is_integer():
        return f"Rs {int(amount_cr):,}cr"
    return f"Rs {amount_cr:,.1f}cr"


_BAND_LABELS = {
    "100_TO_500": "Rs 100-500cr",
    "500_TO_2000": "Rs 500-2,000cr",
    "OVER_2000": "Rs 2,000cr+",
}


def alert_amount(alert):
    """
    Render the size, never letting an estimate look like a stated fact:
      stated               -> Rs 2,000cr
      computed (stake×mcap) -> ~Rs 1,713cr (stake x mkt cap)
      band (unlisted)       -> est. Rs 500-2,000cr
      nothing               -> Size undisclosed
    """
    amt = alert.get("amount_cr")
    src = alert.get("size_source")
    if amt is not None:
        if src == "computed":
            return f"~{format_amount(amt)} (stake x mkt cap)"
        return format_amount(amt)
    if src == "band":
        lbl = _BAND_LABELS.get(alert.get("size_band"))
        if lbl:
            return f"est. {lbl}"
    return "Size undisclosed"


def format_alert(alert):
    emoji = "🔴" if alert.get("confidence") == "high" else "🟡"
    company = _escape(alert.get("company") or "Unknown company")
    if alert.get("is_update"):
        company = f"UPDATE · {company}"

    deal_type = _escape(alert.get("deal_type") or "unknown")
    amount = alert_amount(alert)

    one_line = _escape(alert.get("one_line") or "")

    individuals = alert.get("individuals") or []
    if individuals:
        names = _escape(", ".join(individuals))
    else:
        names = "No individual named"
    if alert.get("buyer"):
        names += f"  ·  buyer: {_escape(alert['buyer'])}"

    source = _escape(alert.get("source") or "source")
    url = alert.get("url") or ""

    note = ""
    if alert.get("is_update") and alert.get("note"):
        note = f"\n_({_escape(alert['note'])})_"

    return (
        f"{emoji} *{company}* · {deal_type} · {amount}\n\n"
        f"_{one_line}_{note}\n\n"
        f"{names}\n\n"
        f"[{source}]({url})"
    )


def send(text, reply_markup=None):
    """Send one message to Telegram. Returns True on success."""
    token, chat_id = _token(), _chat_id()
    if not token or not chat_id:
        print("[notify] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
        return False
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(TELEGRAM_API.format(token=token), json=payload, timeout=30)
        if r.status_code != 200:
            print(f"[notify] Telegram error {r.status_code}: {r.text}")
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[notify] Telegram send failed: {exc}")
        return False


def feedback_keyboard(deal_id):
    """Inline keyboard: Useful / Already knew / Noise. callback_data e.g. 'fb:1423:useful'."""
    return {"inline_keyboard": [[
        {"text": "\U0001F44D Useful", "callback_data": f"fb:{deal_id}:useful"},
        {"text": "\U0001F937 Already knew", "callback_data": f"fb:{deal_id}:already_knew"},
        {"text": "\U0001F5D1 Noise", "callback_data": f"fb:{deal_id}:noise"},
    ]]}


def send_alert(alert):
    markup = feedback_keyboard(alert["deal_id"]) if alert.get("deal_id") else None
    return send(format_alert(alert), reply_markup=markup)


def send_test():
    ok = send("🔴 *Liquidity Radar* test message — if you can read this, "
              "Telegram is wired up correctly.")
    print("Test message sent." if ok else "Test message FAILED — check secrets.")
    return ok
