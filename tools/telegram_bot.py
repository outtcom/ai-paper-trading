"""
Telegram bot for paper trading session notifications.
Uses raw requests to Telegram Bot API — no extra dependencies beyond requests.

Functions:
  send_message(text)              — plain text notification
  send_approval_request(summary)  — trade card with inline Yes/No buttons
  poll_for_response(timeout)      — waits for button tap, returns 'approved'|'rejected'|'timeout'
"""
import json
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
_BASE = f"https://api.telegram.org/bot{_TOKEN}"


def send_message(text: str) -> dict:
    """Send a plain HTML-formatted message to the configured chat."""
    resp = requests.post(
        f"{_BASE}/sendMessage",
        json={"chat_id": _CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=15,
    )
    return resp.json()


def send_approval_request(trade_summary: dict) -> int:
    """
    Send a trade opportunity card with inline [APPROVE] / [SKIP] buttons.
    Returns the Telegram message_id of the sent card.

    Expected keys in trade_summary:
      ticker, current_price, conviction, why, bull_case, bear_case,
      take_profit, take_profit_pct, stop_loss, stop_loss_pct,
      position_size_usd, qty, session_day, total_days
    """
    s = trade_summary
    sign_tp = "+"
    tp_pct = f"{sign_tp}{s['take_profit_pct']:.1f}%"
    sl_pct = f"-{s['stop_loss_pct']:.1f}%"

    text = (
        f"📊 <b>TRADE OPPORTUNITY — Day {s['session_day']}/{s['total_days']}</b>\n\n"
        f"Ticker:     <b>{s['ticker']}</b> @ ${s['current_price']:.2f}\n"
        f"Conviction: {s['conviction'].upper()}\n\n"
        f"<b>WHY THIS TRADE:</b>\n{s['why']}\n\n"
        f"📈 <b>BULL:</b> {s['bull_case']}\n"
        f"📉 <b>BEAR:</b> {s['bear_case']}\n\n"
        f"Entry:  ${s['current_price']:.2f}\n"
        f"TP:     ${s['take_profit']:.2f}  ({tp_pct})\n"
        f"SL:     ${s['stop_loss']:.2f}  ({sl_pct})\n"
        f"Size:   ${s['position_size_usd']:.0f}  ({s['qty']} shares)\n\n"
        f"⏳ <i>Expires in 60 min — no reply = skip</i>"
    )

    keyboard = json.dumps({
        "inline_keyboard": [[
            {"text": "✅ APPROVE", "callback_data": "approve"},
            {"text": "❌ SKIP", "callback_data": "skip"},
        ]]
    })

    resp = requests.post(
        f"{_BASE}/sendMessage",
        json={
            "chat_id": _CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": keyboard,
        },
        timeout=15,
    )
    data = resp.json()
    return data.get("result", {}).get("message_id")


def poll_for_response(timeout_seconds: int = 3600, poll_interval: int = 15) -> str:
    """
    Long-poll Telegram for an inline button callback from the configured chat.

    Returns:
      'approved'  — user tapped APPROVE
      'rejected'  — user tapped SKIP
      'timeout'   — no response within timeout_seconds
    """
    deadline = time.time() + timeout_seconds
    offset = None

    # Flush any stale updates so we don't accidentally pick up old button taps
    try:
        resp = requests.get(f"{_BASE}/getUpdates", params={"timeout": 0, "offset": -1}, timeout=10)
        stale = resp.json().get("result", [])
        if stale:
            offset = stale[-1]["update_id"] + 1
    except Exception:
        pass

    while time.time() < deadline:
        remaining = deadline - time.time()
        wait = min(poll_interval, remaining)
        if wait <= 0:
            break

        try:
            params: dict = {"timeout": int(wait)}
            if offset is not None:
                params["offset"] = offset

            resp = requests.get(
                f"{_BASE}/getUpdates",
                params=params,
                timeout=int(wait) + 10,
            )
            updates = resp.json().get("result", [])

            for update in updates:
                offset = update["update_id"] + 1
                cb = update.get("callback_query")
                if not cb:
                    continue

                # Accept responses from the configured chat only
                if str(cb.get("message", {}).get("chat", {}).get("id")) != str(_CHAT_ID):
                    continue

                action = cb.get("data")
                # Dismiss the "loading" spinner on the button
                requests.post(
                    f"{_BASE}/answerCallbackQuery",
                    json={
                        "callback_query_id": cb["id"],
                        "text": "Got it! Trade approved." if action == "approve" else "Got it! Trade skipped.",
                    },
                    timeout=10,
                )
                return "approved" if action == "approve" else "rejected"

        except requests.exceptions.Timeout:
            pass  # long-poll timeout is normal, just loop again
        except Exception as e:
            print(f"[telegram] poll error: {e}")
            time.sleep(5)

    return "timeout"
