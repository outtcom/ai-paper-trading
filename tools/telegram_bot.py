"""
Telegram bot for paper trading session notifications.
Uses raw requests to Telegram Bot API — no extra dependencies beyond requests.

Functions:
  send_message(text, chat_id)          — send to a specific chat (private by default)
  broadcast_message(text)              — send to both private chat AND group
  send_group_trade_signal(signal)      — informational trade card to group only (no buttons)
  send_approval_request(summary)       — trade card with inline Yes/No buttons (private only)
  poll_for_response(timeout)           — waits for button tap, returns 'approved'|'rejected'|'timeout'
"""
import json
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
_GROUP_ID = os.environ.get("TELEGRAM_GROUP_CHAT_ID", "")
_BASE     = f"https://api.telegram.org/bot{_TOKEN}"

# Dashboard footer appended to every outbound message
_FOOTER = '\n\n📊 <a href="https://outtcom.github.io/ai-paper-trading/">Live Dashboard</a>'


def send_message(text: str, chat_id: str = None) -> dict:
    """Send a plain HTML-formatted message to the given chat (defaults to private chat)."""
    target = chat_id or _CHAT_ID
    resp = requests.post(
        f"{_BASE}/sendMessage",
        json={"chat_id": target, "text": text + _FOOTER, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=15,
    )
    return resp.json()


def broadcast_message(text: str) -> dict:
    """Send to both private chat and group. Returns result from private chat."""
    result = send_message(text, chat_id=_CHAT_ID)
    if _GROUP_ID:
        send_message(text, chat_id=_GROUP_ID)
    return result


def send_group_trade_signal(signal: dict) -> dict:
    """
    Send an informational trade signal card to the group only (no buttons).
    Works for both swing trade summaries and day trade signals.

    For day trade signals, expected keys: ticker, signal_type, entry_price,
      target_price, target_pct, stop_price, stop_pct, rationale (optional)
    For swing trade summaries (mapped from approval card), expected keys:
      ticker, conviction, current_price, take_profit, take_profit_pct,
      stop_loss, stop_loss_pct, why, direction (optional)
    """
    if not _GROUP_ID:
        return {}

    # Day trade signal card
    if signal.get("signal_type") in ("gap_and_go", "momentum_breakout"):
        label = "Gap & Go" if signal["signal_type"] == "gap_and_go" else "Momentum Breakout"
        text = (
            f"📡 <b>{label} Signal — {signal['ticker']}</b>\n\n"
            f"Entry: <b>${signal['entry_price']:.2f}</b>\n"
            f"Target: ${signal['target_price']:.2f} (+{signal['target_pct']:.1f}%)\n"
            f"Stop:   ${signal['stop_price']:.2f} (-{signal['stop_pct']:.1f}%)\n"
            f"Auto-close: {signal.get('auto_close_date', 'EOD')}\n"
            f"\n<i>Paper signal only — no capital allocated</i>"
        )
        if signal.get("rationale"):
            text += f"\n\nRationale: {signal['rationale']}"
    else:
        # Swing trade informational card (mirrors approval card, no buttons)
        direction = signal.get("direction", "long")
        dir_tag   = " 🔻 SHORT" if direction == "short" else ""
        text = (
            f"📊 <b>Trade Signal{dir_tag} — {signal['ticker']}</b>\n\n"
            f"Price: ${signal.get('current_price', signal.get('entry_price', 0)):.2f}  |  "
            f"Conviction: {signal.get('conviction', '—').upper()}\n\n"
            f"<b>Why:</b> {signal.get('why', signal.get('rationale', '—'))}\n\n"
            f"Entry: ${signal.get('current_price', signal.get('entry_price', 0)):.2f}\n"
            f"TP:    ${signal['take_profit']:.2f} (+{signal['take_profit_pct']:.1f}%)\n"
            f"SL:    ${signal['stop_loss']:.2f} (-{signal['stop_loss_pct']:.1f}%)\n"
            f"Size:  ${signal.get('position_size_usd', 0):.0f}  ({signal.get('qty', 0)} shares)\n\n"
            f"<i>Trade pending approval</i>"
        )
        if signal.get("vix_label"):
            text += f"\nVIX: {signal['vix_label']}"

    resp = requests.post(
        f"{_BASE}/sendMessage",
        json={"chat_id": _GROUP_ID, "text": text + _FOOTER,
              "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=15,
    )
    return resp.json()


def send_approval_request(trade_summary: dict) -> int:
    """
    Send a trade opportunity card with inline [APPROVE] / [SKIP] buttons to PRIVATE chat only.
    Returns the Telegram message_id of the sent card.

    Expected keys in trade_summary:
      ticker, current_price, conviction, why, bull_case, bear_case,
      take_profit, take_profit_pct, stop_loss, stop_loss_pct,
      position_size_usd, qty, session_day, total_days, direction (optional)
    """
    s = trade_summary
    direction = s.get("direction", "long")
    dir_tag   = "  🔻 SHORT" if direction == "short" else ""

    text = (
        f"📊 <b>TRADE OPPORTUNITY{dir_tag} — Day {s['session_day']}/{s['total_days']}</b>\n\n"
        f"Ticker:     <b>{s['ticker']}</b> @ ${s['current_price']:.2f}\n"
        f"Conviction: {s['conviction'].upper()}\n\n"
        f"<b>WHY THIS TRADE:</b>\n{s['why']}\n\n"
        f"📈 <b>BULL:</b> {s['bull_case']}\n"
        f"📉 <b>BEAR:</b> {s['bear_case']}\n\n"
        f"Entry:  ${s['current_price']:.2f}\n"
        f"TP:     ${s['take_profit']:.2f}  ({'–' if direction == 'short' else '+'}{s['take_profit_pct']:.1f}%)\n"
        f"SL:     ${s['stop_loss']:.2f}  ({'+'  if direction == 'short' else '–'}{s['stop_loss_pct']:.1f}%)\n"
        f"Size:   ${s['position_size_usd']:.0f}  ({s['qty']} shares)\n\n"
        f"⏳ <i>Expires in 60 min — no reply = skip</i>"
        + (f"\n\n📉 VIX: {s['vix_label']}" if s.get("vix_label") else "")
    )

    keyboard = json.dumps({
        "inline_keyboard": [[
            {"text": "✅ APPROVE", "callback_data": "approve"},
            {"text": "❌ SKIP",    "callback_data": "skip"},
        ]]
    })

    resp = requests.post(
        f"{_BASE}/sendMessage",
        json={
            "chat_id": _CHAT_ID,
            "text": text + _FOOTER,
            "parse_mode": "HTML",
            "reply_markup": keyboard,
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    data = resp.json()
    return data.get("result", {}).get("message_id")


def poll_for_response(timeout_seconds: int = 3600, poll_interval: int = 15) -> str:
    """
    Long-poll Telegram for an inline button callback from the configured private chat.

    Returns:
      'approved'  — user tapped APPROVE
      'rejected'  — user tapped SKIP
      'timeout'   — no response within timeout_seconds
    """
    deadline = time.time() + timeout_seconds
    offset = None

    # Drain ALL pending updates so stale button taps from previous runs can't
    # auto-approve a new trade.
    try:
        while True:
            resp = requests.get(
                f"{_BASE}/getUpdates",
                params={"timeout": 0, "limit": 100},
                timeout=10,
            )
            stale = resp.json().get("result", [])
            if not stale:
                break
            offset = stale[-1]["update_id"] + 1
            requests.get(
                f"{_BASE}/getUpdates",
                params={"timeout": 0, "offset": offset},
                timeout=10,
            )
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

                # Accept responses from the private chat only
                if str(cb.get("message", {}).get("chat", {}).get("id")) != str(_CHAT_ID):
                    continue

                action = cb.get("data")
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
            pass
        except Exception as e:
            print(f"[telegram] poll error: {e}")
            time.sleep(5)

    return "timeout"
