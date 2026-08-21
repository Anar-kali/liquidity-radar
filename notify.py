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

    # Who gets paid, in priority order: named individuals first, then the
    # selling entity. The seller line is the point of the alert — the buyer is
    # spending money, so it is deliberately not shown at all any more.
    individuals = alert.get("individuals") or []
    seller = alert.get("seller")
    if individuals:
        names = f"💰 {_escape(', '.join(individuals))}"
        # Only add the entity when it isn't just the same name again — for a
        # promoter sale the model routinely fills both with the one person.
        if seller and seller.strip().lower() not in {i.strip().lower() for i in individuals}:
            names += f"  ·  via {_escape(seller)}"
    elif seller:
        names = f"💰 {_escape(seller)} (selling)"
    else:
        names = "No seller named"

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


def format_confirmed_alert(row):
    """
    CONFIRMED alert (v3 Change A) — a bulk/block deal or PIT disclosure.
    The money here is a fact, not a classifier estimate; visually distinct
    (green, not red/yellow) from news-sourced alerts.

        [GREEN] CONFIRMED · Security Name · block deal · Rs 2,000cr

        Client Name sold 150,000 shares at Rs 245.50

        Settles T+1 · trade date 07-Aug-2026
        NSE daily deal file
    """
    prefix = "CONFIRMED UPDATE" if row.get("is_update") else "CONFIRMED"
    security = _escape(row.get("security_name") or "Unknown company")
    deal_type = _escape(row.get("deal_type") or "block deal")
    amount = format_amount(row.get("value_cr"))

    client = _escape(row.get("client_name") or "Unnamed seller")
    qty = row.get("quantity")
    price = row.get("price")
    qty_line = f"{client} sold"
    if qty:
        qty_line += f" {int(qty):,} shares"
    if price:
        qty_line += f" at Rs {price:g}"

    settle_line = "Settles T+1"
    if row.get("trade_date"):
        settle_line += f" · trade date {_escape(row['trade_date'])}"

    exchange = _escape(row.get("exchange") or "Exchange")
    source_label = "daily PIT feed" if row.get("deal_type") == "PIT disclosure" else "daily deal file"

    return (
        f"\U0001F7E2 {prefix} · *{security}* · {deal_type} · {amount}\n\n"
        f"{qty_line}\n\n"
        f"{settle_line}\n"
        f"{exchange} {source_label}"
    )


def send_confirmed_alert(row, deal_id=None):
    markup = feedback_keyboard(deal_id) if deal_id else None
    return send(format_confirmed_alert(row), reply_markup=markup)


def format_pattern_alert(person_name, company, total_cr, transactions, weeks):
    """
    PATTERN alert (v3 Change B) — several sub-threshold sales that add up.
    Visually distinct from both news and CONFIRMED alerts.

        [PURPLE] PATTERN · Person Name · Company · Rs 360cr over 3 sales

        2026-06-01  Rs 120cr
        2026-06-20  Rs 120cr
        2026-07-15  Rs 120cr

        6 weeks · no single sale crossed the threshold
    """
    person = _escape(person_name or "Unnamed individual")
    co = _escape(company or "Unknown company")
    total = format_amount(total_cr)
    n = len(transactions)

    lines = [f"\U0001F7E3 PATTERN · *{person}* · {co} · {total} over {n} sales", ""]
    # Cap the displayed rows — a genuine high-frequency salami-slice pattern
    # (the exact case this alert type exists to catch) can otherwise exceed
    # Telegram's 4096-character message limit and silently fail to send.
    # Show the most recent MAX_SHOWN and summarise the rest.
    MAX_SHOWN = 20
    shown = transactions[-MAX_SHOWN:] if n > MAX_SHOWN else transactions
    if n > MAX_SHOWN:
        hidden_total = sum(v for _, v in transactions[:-MAX_SHOWN])
        lines.append(f"… {n - MAX_SHOWN} earlier sales totalling {format_amount(hidden_total)} …")
    for trade_date, value_cr in shown:
        lines.append(f"{_escape(trade_date)}  {format_amount(value_cr)}")
    lines.append("")
    lines.append(f"{weeks} weeks · no single sale crossed the threshold")
    return "\n".join(lines)


def send_pattern_alert(person_name, company, total_cr, transactions, weeks):
    return send(format_pattern_alert(person_name, company, total_cr, transactions, weeks))


def send_test():
    ok = send("🔴 *Liquidity Radar* test message — if you can read this, "
              "Telegram is wired up correctly.")
    print("Test message sent." if ok else "Test message FAILED — check secrets.")
    return ok
