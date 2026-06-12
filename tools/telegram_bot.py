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

    # Mid-cap / penny signal card
    _DT_LABELS = {
        "midcap_breakout": "Mid-Cap Breakout",
        "penny_breakout":  "Penny Breakout",
    }
    if signal.get("signal_type") in _DT_LABELS:
        label     = _DT_LABELS[signal["signal_type"]]
        qty       = signal.get("qty", 0)
        alloc     = signal.get("allocated_usd", 0)
        stype     = signal["signal_type"]
        pool      = "$5K penny pool" if stype == "penny_breakout" else "$5K mid-cap pool"
        size_line = (f"Size:   <b>{qty} shares (${alloc:,.0f})</b> from {pool}\n"
                     if qty > 0 else "Size:   calculating...\n")
        closes = signal.get("auto_close_date", "EOD")

        direction  = signal.get("direction", "long")
        tp_sign    = "+" if direction == "long" else "-"
        sl_sign    = "-" if direction == "long" else "+"
        text = (
            f"📡 <b>{label} — {signal['ticker']}</b>\n\n"
            f"Direction: <b>{direction.upper()}</b>\n"
            f"Entry:  <b>${signal['entry_price']:.2f}</b>\n"
            f"Target: ${signal['target_price']:.2f} ({tp_sign}{signal['target_pct']:.2f}%)\n"
            f"Stop:   ${signal['stop_price']:.2f} ({sl_sign}{signal['stop_pct']:.2f}%)\n"
        )
        if signal.get("rrr"):
            text += f"RRR:    {signal['rrr']:.1f}:1\n"
        text += f"{size_line}Closes: {closes}\n"

        if stype == "scalping_fvg":
            if signal.get("fvg_top") and signal.get("fvg_bottom"):
                text += f"FVG:    ${signal['fvg_bottom']:.2f} – ${signal['fvg_top']:.2f}\n"
            if signal.get("displacement_pct"):
                text += (f"Disp:   {signal['displacement_pct']:+.2f}%  "
                         f"(${signal.get('displacement_low', 0):.2f}–${signal.get('displacement_high', 0):.2f})\n")
        elif stype == "scalping_orb" and signal.get("range_high") and signal.get("range_low"):
            text += f"Range:  ${signal['range_low']:.2f} – ${signal['range_high']:.2f} (30-min ORB)\n"

        if signal.get("rationale"):
            text += f"\n<i>{signal['rationale']}</i>"
    else:
        # Swing trade informational card (group-only, informational)
        direction = signal.get("direction", "long")
        dir_tag   = " 🔻 SHORT" if direction == "short" else ""
        tp_sign   = "–" if direction == "short" else "+"
        sl_sign   = "+" if direction == "short" else "–"
        why_full  = (signal.get("why") or signal.get("rationale") or "—").strip()
        news_lines = signal.get("top_news", [])
        news_block = ("\n\n📰 <b>Top News:</b>\n" + "\n".join(f"  • {h}" for h in news_lines[:3])) if news_lines else ""
        text = (
            f"📊 <b>Trade Signal{dir_tag} — {signal['ticker']}</b>\n\n"
            f"Price: ${signal.get('current_price', signal.get('entry_price', 0)):.2f}  |  "
            f"Conviction: {signal.get('conviction', '—').upper()}\n\n"
            f"<b>Why this trade:</b>\n{why_full}\n\n"
            f"Entry: ${signal.get('current_price', signal.get('entry_price', 0)):.2f}\n"
            f"TP:    ${signal['take_profit']:.2f} ({tp_sign}{signal['take_profit_pct']:.1f}%)\n"
            f"SL:    ${signal['stop_loss']:.2f} ({sl_sign}{signal['stop_loss_pct']:.1f}%)\n"
            f"Size:  ${signal.get('position_size_usd', 0):.0f}  ({signal.get('qty', 0)} shares)"
            f"{news_block}"
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


def send_trade_notification(trade_summary: dict) -> dict:
    """
    Send informational swing-trade card to BOTH private chat and group (no buttons).
    Trade is auto-executing immediately — no approval needed.

    Expected keys: ticker, current_price, conviction, why, bull_case, bear_case,
      take_profit, take_profit_pct, stop_loss, stop_loss_pct,
      position_size_usd, qty, session_day, total_days, direction (optional),
      top_news (optional list of headline strings)
    """
    s         = trade_summary
    direction = s.get("direction", "long")
    dir_tag   = "  🔻 SHORT" if direction == "short" else ""
    tp_sign   = "–" if direction == "short" else "+"
    sl_sign   = "+" if direction == "short" else "–"

    # ── Reasoning (full, no truncation) ──────────────────────────────────
    why = (s.get("why") or "").strip()

    # ── Bull / Bear cases (first 350 chars each — long enough to be useful) ──
    bull_raw = (s.get("bull_case") or "").strip()
    bear_raw = (s.get("bear_case") or "").strip()
    # Extract just the top 3 bullet points / sentences to keep it scannable
    def _top_lines(text: str, max_chars: int = 350) -> str:
        if not text:
            return "N/A"
        # If the text has bullet points, take the first 3
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        # Prefer bullet lines
        bullets = [l for l in lines if l.startswith(("-", "•", "*", "1", "2", "3"))]
        chosen = bullets[:3] if bullets else lines[:4]
        result = "\n".join(chosen)
        return result[:max_chars] + ("…" if len(result) > max_chars else "")

    bull_text = _top_lines(bull_raw)
    bear_text = _top_lines(bear_raw)

    # ── News headlines ────────────────────────────────────────────────────
    top_news = s.get("top_news", [])
    news_block = ""
    if top_news:
        news_lines = "\n".join(f"  • {h}" for h in top_news[:3])
        news_block = f"\n\n📰 <b>Top News:</b>\n{news_lines}"

    # ── VIX / regime context ──────────────────────────────────────────────
    regime_line = ""
    if s.get("vix_label"):
        regime_line = f"\n\nVIX: {s['vix_label']}"

    text = (
        f"📊 <b>TRADE EXECUTING{dir_tag}</b> — Day {s['session_day']}/{s['total_days']}\n"
        f"<b>{s['ticker']}</b>  @  ${s['current_price']:.2f}  ·  Conviction: <b>{s['conviction'].upper()}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 <b>Entry:</b>  ${s['current_price']:.2f}\n"
        f"✅ <b>Target:</b> ${s['take_profit']:.2f} ({tp_sign}{s['take_profit_pct']:.1f}%)\n"
        f"🛑 <b>Stop:</b>   ${s['stop_loss']:.2f} ({sl_sign}{s['stop_loss_pct']:.1f}%)\n"
        f"💰 <b>Size:</b>   ${s['position_size_usd']:.0f}  ({s['qty']} shares)\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>Why this trade:</b>\n{why}\n\n"
        f"📈 <b>Bull case:</b>\n{bull_text}\n\n"
        f"📉 <b>Bear case:</b>\n{bear_text}"
        f"{news_block}"
        f"{regime_line}\n\n"
        f"<i>Auto-executing now</i>"
    )
    return broadcast_message(text)


def send_full_agent_chain(state: dict) -> None:
    """
    Send the complete multi-agent reasoning chain as three follow-up Telegram messages
    immediately after the main trade card. Sent to private chat only (detailed logs).

    Message 1/3: Fundamental + Sentiment analyst reports (full text)
    Message 2/3: Technical analyst + Bull/Bear researcher full reports
    Message 3/3: Trader decision + Risk Manager assessment + Fund Manager final reasoning
    """
    ticker = state.get("ticker", "?")
    header = f"<b>📋 FULL ANALYSIS — {ticker}</b>"

    def _safe(text) -> str:
        if not text:
            return "<i>Not available</i>"
        if isinstance(text, dict):
            import json as _json
            return f"<code>{_json.dumps(text, indent=2)[:3000]}</code>"
        return str(text).strip()

    # Message 1/3 — Fundamental + Sentiment
    fund_text = _safe(state.get("fundamental_report"))
    sent_text = _safe(state.get("sentiment_report"))
    msg1 = (
        f"{header} <i>(1/3)</i>\n\n"
        f"━━ <b>[1/7] Fundamental Analyst</b> ━━\n"
        f"{fund_text[:3000]}\n\n"
        f"━━ <b>[2/7] Sentiment Analyst</b> ━━\n"
        f"{sent_text[:3000]}"
    )
    send_message(msg1, chat_id=_CHAT_ID)

    # Message 2/3 — Technical + Bull/Bear Researcher
    tech_text = _safe(state.get("technical_report"))
    bull_text = _safe(state.get("bull_case"))
    bear_text = _safe(state.get("bear_case"))
    msg2 = (
        f"{header} <i>(2/3)</i>\n\n"
        f"━━ <b>[3/7] Technical Analyst</b> ━━\n"
        f"{tech_text[:2500]}\n\n"
        f"━━ <b>[4/7] Bull Researcher</b> ━━\n"
        f"{bull_text[:1200]}\n\n"
        f"━━ <b>[5/7] Bear Researcher</b> ━━\n"
        f"{bear_text[:1200]}"
    )
    send_message(msg2, chat_id=_CHAT_ID)

    # Message 3/3 — Trader + Risk Manager + Fund Manager
    trader  = state.get("trader_decision", {})
    risk    = state.get("risk_adjusted_decision", {})
    fm      = state.get("final_order", {})

    trader_text = (
        f"Action: <b>{str(trader.get('action', '?')).upper()}</b>  "
        f"Conviction: <b>{str(trader.get('conviction', '?')).upper()}</b>\n"
        f"{_safe(trader.get('reasoning'))}"
    )
    risk_text = (
        f"Assessment: <b>{str(risk.get('risk_assessment', '?')).upper()}</b>  "
        f"Position: <b>{float(risk.get('final_position_size') or 0) * 100:.0f}%</b>  "
        f"SL: <b>{float(risk.get('stop_loss_pct') or 0) * 100:.1f}%</b>\n"
        f"{_safe(risk.get('reasoning'))}"
    )
    fm_text = (
        f"Action: <b>{str(fm.get('action', '?')).upper()}</b>  "
        f"Qty: <b>{fm.get('qty', '?')}</b>  "
        f"Size: <b>{float(fm.get('position_size_pct') or 0) * 100:.0f}%</b>\n"
        f"{_safe(fm.get('final_reasoning'))}"
    )
    msg3 = (
        f"{header} <i>(3/3)</i>\n\n"
        f"━━ <b>[6/7] Trader Decision</b> ━━\n"
        f"{trader_text[:2000]}\n\n"
        f"━━ <b>[7a] Risk Manager</b> ━━\n"
        f"{risk_text[:2000]}\n\n"
        f"━━ <b>[7b] Fund Manager (Final Gate)</b> ━━\n"
        f"{fm_text[:2000]}"
    )
    send_message(msg3, chat_id=_CHAT_ID)


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
