"""
Midday position monitor — runs at 12:00 PM ET.
Triggered by GitHub Actions daily.

Checks open positions and sends an alert if any position is:
  - Within 75%+ of the way to TP (almost there — consider riding it)
  - Within 75%+ of the way to SL (danger zone — mentally prepare)
  - Within 75%+ of the way to partial profit level (1:1 R/R incoming)

This closes the 5-hour gap between market open and the 3:30 PM pre-close alert.
Only sends a message if there are open positions.
"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.market_data import get_latest_price, _yahoo_intraday_ohlcv
from tools.session_manager import (
    get_portfolio, get_session_day, update_last_price,
    get_open_scalping_signals, close_scalping_signal,
)
from tools.telegram_bot import broadcast_message

PROXIMITY_THRESHOLD = 0.75   # alert when 75%+ of the way to TP or SL


def _resolve_scalping_signals(today: str) -> list:
    """
    Close all open scalping signals at noon.
    Replays intraday bars to check if TP or SL was hit during the morning.
    Falls back to current live price if bars unavailable.
    Returns list of closed signal dicts.
    """
    signals  = get_open_scalping_signals()
    resolved = []
    for signal in signals:
        if signal.get("auto_close_date", "") > today:
            continue
        ticker    = signal["ticker"]
        direction = signal.get("direction", "long")
        tp        = signal["target_price"]
        sl        = signal["stop_price"]
        gen_date  = signal["generated_date"]

        # Replay 5-min bars to find first TP or SL hit
        bars       = _yahoo_intraday_ohlcv(ticker, gen_date, "5m")
        exit_price = None
        for bar in bars:
            if direction == "long":
                if bar["high"] >= tp:
                    exit_price = tp; break   # TP hit
                if bar["low"]  <= sl:
                    exit_price = sl; break   # SL hit
            else:  # short
                if bar["low"]  <= tp:
                    exit_price = tp; break   # TP hit
                if bar["high"] >= sl:
                    exit_price = sl; break   # SL hit

        if exit_price is None:
            exit_price = get_latest_price(ticker)  # close at noon price

        closed  = close_scalping_signal(signal["id"], exit_price, today)
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

    # Resolve any open scalping signals — they auto-close at noon
    scalp_resolved = _resolve_scalping_signals(today)

    positions = portfolio.get("positions", {})

    if not positions:
        print("[midday] No open positions. Skipping midday alert.")
        return

    session_day = get_session_day()
    total_days = portfolio["session"]["total_days"]

    lines = [f"🕛 <b>MIDDAY CHECK — Day {session_day}/{total_days}</b>\n"]

    alerts = []
    normal = []

    for ticker, pos in positions.items():
        try:
            price      = get_latest_price(ticker)
            update_last_price(ticker, price)  # keep dashboard unrealized P&L current
            entry      = pos["entry_price"]
            tp         = pos["take_profit"]
            sl         = pos["stop_loss"]
            qty        = pos["qty"]
            partial_taken = pos.get("partial_taken", False)
            partial_price = pos.get("partial_profit_price", round(entry * (1 + pos.get("stop_loss_pct", 3) / 100), 2))

            unr     = round((price - entry) * qty, 2)
            unr_pct = round((price - entry) / entry * 100, 2)
            sign    = "+" if unr >= 0 else ""

            # Distance calculations
            tp_range    = abs(tp - entry)
            sl_range    = abs(sl - entry)
            to_tp       = abs(tp - price)
            to_sl       = abs(price - sl)

            # How far along are we? (0 = just entered, 1 = at TP or SL)
            pct_to_tp = (tp_range - to_tp) / tp_range if tp_range > 0 else 0
            pct_to_sl = (sl_range - to_sl) / sl_range if sl_range > 0 else 0
            pct_to_partial = 0
            if not partial_taken and partial_price > entry:
                partial_range = abs(partial_price - entry)
                pct_to_partial = (partial_range - abs(partial_price - price)) / partial_range if partial_range > 0 else 0

            flags = []
            if price >= tp:
                flags.append("🎯 AT/ABOVE TAKE PROFIT")
            elif pct_to_tp >= PROXIMITY_THRESHOLD:
                flags.append(f"🎯 {pct_to_tp*100:.0f}% of way to TP — almost there!")

            if price <= sl:
                flags.append("🛑 AT/BELOW STOP LOSS")
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

    # Always show alerts first
    if alerts:
        lines.append("🔔 <b>Action Items:</b>")
        lines.extend(alerts)
        lines.append("")

    if normal:
        lines.append("<b>Monitoring:</b>")
        lines.extend(normal)
        lines.append("")

    equity = portfolio.get("equity", portfolio["initial_capital"])
    initial = portfolio["initial_capital"]
    ret = round((equity - initial) / initial * 100, 2)
    sign = "+" if ret >= 0 else ""
    lines.append(
        f"<i>Session equity: ${equity:,.2f} ({sign}{ret:.1f}%)\n"
        f"Next update: Pre-close alert at 3:30 PM ET.</i>"
    )

    # Append scalping resolution summary if any signals closed
    if scalp_resolved:
        scalp_lines = ["\n⚡ <b>ORB Scalping — Noon Close:</b>"]
        for s in scalp_resolved:
            pnl  = s.get("pnl_pct", 0) or 0
            icon = "✅" if s.get("outcome") == "win" else ("❌" if s.get("outcome") == "loss" else "➖")
            scalp_lines.append(
                f"  {icon} {s['ticker']} {s.get('direction','').upper()} "
                f"{'+' if pnl >= 0 else ''}{pnl:.2f}% → {(s.get('outcome') or '?').upper()}"
            )
        sc = get_portfolio().get("scalping_capital", {})
        scalp_lines.append(f"<i>Scalping pool: ${sc.get('equity', 5000):,.2f}</i>")
        lines.extend(scalp_lines)

    broadcast_message("\n".join(lines))
    print(f"[midday] Alert sent. {len(alerts)} action items, {len(normal)} monitoring, "
          f"{len(scalp_resolved)} scalping signal(s) closed.")


if __name__ == "__main__":
    main()
