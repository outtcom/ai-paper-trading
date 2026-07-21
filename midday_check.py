"""
Midday position monitor — runs at 12:00 PM ET.
Triggered by GitHub Actions daily.

Checks open swing positions and sends an alert if any is:
  - Within 75%+ of the way to TP (almost there — consider riding it)
  - Within 75%+ of the way to SL (danger zone — mentally prepare)
  - Within 75%+ of the way to partial profit level (1:1 R/R incoming)

Also checks open penny signals mid-session (intraday replay) and closes any that
already hit their TP or SL, since penny stocks can move the full range in hours.

This closes the 5-hour gap between market open and the 3:30 PM pre-close alert.
"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.market_data import get_latest_price, _yahoo_intraday_ohlcv
from tools.session_manager import (
    get_portfolio, get_session_day, update_last_price,
    get_open_penny_signals, close_penny_signal,
)
from tools.telegram_bot import broadcast_message

PROXIMITY_THRESHOLD = 0.75   # alert when 75%+ of the way to TP or SL


def _check_penny_signals(today: str) -> list:
    """
    Replay intraday 5-min bars for open penny signals to check if TP or SL
    was already hit during the morning session. Close immediately if so.
    Penny stocks can complete a full move (±8%/5%) within the first 2 hours.
    Falls back to current live price if intraday bars are unavailable.
    Returns list of closed signal dicts.
    """
    signals  = get_open_penny_signals()
    resolved = []
    for signal in signals:
        if signal.get("auto_close_date", "9999-99-99") < today:
            continue  # already expired — EOD resolver handles these
        ticker    = signal["ticker"]
        direction = signal.get("direction", "long")
        tp        = signal["target_price"]
        sl        = signal["stop_price"]
        gen_date  = signal["generated_date"]

        # Replay 5-min bars looking for the first TP or SL touch
        bars       = _yahoo_intraday_ohlcv(ticker, gen_date, "5m")
        exit_price = None
        for bar in bars:
            if direction == "long":
                if bar["high"] >= tp:
                    exit_price = tp; break
                if bar["low"]  <= sl:
                    exit_price = sl; break
            else:
                if bar["low"]  <= tp:
                    exit_price = tp; break
                if bar["high"] >= sl:
                    exit_price = sl; break

        if exit_price is None:
            continue  # neither TP nor SL hit yet — leave open for EOD

        closed = close_penny_signal(signal["id"], exit_price, today)
        if closed:
            resolved.append(closed)
    return resolved


def main():
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    print(f"\n[midday] ========== Midday Check {today} ==========")

    portfolio = get_portfolio()

    if not portfolio["session"]["active"]:
        print("[midday] No active session. Exiting.")
        return

    # Check penny signals — close intraday if TP/SL already hit
    penny_resolved = _check_penny_signals(today)

    positions = portfolio.get("positions", {})

    if not positions:
        print("[midday] No open positions. Skipping midday alert.")
        if penny_resolved:
            _send_penny_summary(penny_resolved, portfolio)
        return

    session_day = get_session_day()
    total_days = portfolio["session"]["total_days"]

    lines = [f"🕛 <b>MIDDAY CHECK — Day {session_day}/{total_days}</b>\n"]

    alerts = []
    normal = []

    for ticker, pos in positions.items():
        try:
            price      = get_latest_price(ticker)
            update_last_price(ticker, price)
            entry      = pos["entry_price"]
            tp         = pos["take_profit"]
            sl         = pos["stop_loss"]
            qty        = pos["qty"]
            direction     = pos.get("direction", "long")
            partial_taken = pos.get("partial_taken", False)
            partial_price = pos.get("partial_profit_price")
            if not partial_price:
                sl_pct_val = pos.get("stop_loss_pct", 3) / 100
                partial_price = round(
                    entry * (1 - sl_pct_val) if direction == "short" else entry * (1 + sl_pct_val), 2
                )

            if direction == "short":
                unr     = round((entry - price) * qty, 2)
                unr_pct = round((entry - price) / entry * 100, 2)
            else:
                unr     = round((price - entry) * qty, 2)
                unr_pct = round((price - entry) / entry * 100, 2)
            sign = "+" if unr >= 0 else ""

            tp_range    = abs(tp - entry)
            sl_range    = abs(sl - entry)
            to_tp       = abs(tp - price)
            to_sl       = abs(price - sl)

            pct_to_tp = (tp_range - to_tp) / tp_range if tp_range > 0 else 0
            pct_to_sl = (sl_range - to_sl) / sl_range if sl_range > 0 else 0
            if not partial_taken:
                partial_range  = abs(partial_price - entry)
                pct_to_partial = (partial_range - abs(partial_price - price)) / partial_range if partial_range > 0 else 0
            else:
                pct_to_partial = 0

            flags = []
            tp_hit = (price <= tp) if direction == "short" else (price >= tp)
            if tp_hit:
                flags.append("🎯 AT/NEAR TAKE PROFIT")
            elif pct_to_tp >= PROXIMITY_THRESHOLD:
                flags.append(f"🎯 {pct_to_tp*100:.0f}% of way to TP — almost there!")

            sl_hit = (price >= sl) if direction == "short" else (price <= sl)
            if sl_hit:
                flags.append("🛑 AT/NEAR STOP LOSS")
            elif pct_to_sl >= PROXIMITY_THRESHOLD:
                flags.append(f"🚨 {pct_to_sl*100:.0f}% of way to SL — danger zone!")

            if not partial_taken and pct_to_partial >= PROXIMITY_THRESHOLD:
                flags.append(f"💰 {pct_to_partial*100:.0f}% of way to partial profit level")

            emoji = "📈" if unr >= 0 else "📉"
            partial_note = " (partial taken, running on house money)" if partial_taken else ""

            position_summary = (
                f"{emoji} <b>{ticker}</b>{partial_note}\n"
                f"  Price: ${price:.2f}  |  Entry: ${entry:.2f}\n"
                f"  P&amp;L: {sign}${unr:.2f} ({sign}{unr_pct:.1f}%)\n"
                f"  TP: ${tp:.2f}  |  SL: ${sl:.2f}"
            )

            if flags:
                position_summary += "\n  " + "\n  ".join(flags)
                alerts.append(position_summary)
            else:
                normal.append(position_summary)

        except Exception as e:
            normal.append(f"⚠️ <b>{ticker}</b>: price unavailable ({e})")

    if alerts:
        lines.append("🔔 <b>Action Items:</b>")
        lines.extend(alerts)
        lines.append("")

    if normal:
        lines.append("<b>Monitoring:</b>")
        lines.extend(normal)
        lines.append("")

    equity  = portfolio.get("equity", portfolio["initial_capital"])
    initial = portfolio["initial_capital"]
    ret     = round((equity - initial) / initial * 100, 2)
    sign    = "+" if ret >= 0 else ""
    lines.append(
        f"<i>Session equity: ${equity:,.2f} ({sign}{ret:.1f}%)\n"
        f"Next update: Pre-close alert at 3:30 PM ET.</i>"
    )

    # Append penny signal summary if any closed intraday
    if penny_resolved:
        penny_lines = ["\n🪙 <b>Penny Signals — Intraday Close:</b>"]
        for s in penny_resolved:
            pnl  = s.get("pnl_pct", 0) or 0
            icon = "✅" if s.get("outcome") == "win" else ("❌" if s.get("outcome") == "loss" else "➖")
            penny_lines.append(
                f"  {icon} {s['ticker']} {'+' if pnl >= 0 else ''}{pnl:.2f}% → {(s.get('outcome') or '?').upper()}"
            )
        pc = get_portfolio().get("penny_capital", {})
        penny_lines.append(f"<i>Penny pool: ${pc.get('equity', 5000):,.2f}</i>")
        lines.extend(penny_lines)

    broadcast_message("\n".join(lines))
    print(f"[midday] Alert sent. {len(alerts)} action items, {len(normal)} monitoring, "
          f"{len(penny_resolved)} penny signal(s) closed intraday.")


def _send_penny_summary(penny_resolved: list, portfolio: dict) -> None:
    """Send a brief message when penny signals closed but no swing positions are open."""
    if not penny_resolved:
        return
    lines = ["🪙 <b>Penny Signals — Intraday Close:</b>"]
    for s in penny_resolved:
        pnl  = s.get("pnl_pct", 0) or 0
        icon = "✅" if s.get("outcome") == "win" else ("❌" if s.get("outcome") == "loss" else "➖")
        lines.append(f"  {icon} {s['ticker']} {'+' if pnl >= 0 else ''}{pnl:.2f}% → {(s.get('outcome') or '?').upper()}")
    pc = portfolio.get("penny_capital", {})
    lines.append(f"<i>Penny pool: ${pc.get('equity', 5000):,.2f}</i>")
    broadcast_message("\n".join(lines))


if __name__ == "__main__":
    main()
