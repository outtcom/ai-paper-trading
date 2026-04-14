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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.market_data import get_latest_price
from tools.session_manager import get_portfolio, get_session_day
from tools.telegram_bot import send_message

PROXIMITY_THRESHOLD = 0.75   # alert when 75%+ of the way to TP or SL


def main():
    today = datetime.today().strftime("%Y-%m-%d")
    print(f"\n[midday] ========== Midday Check {today} ==========")

    portfolio = get_portfolio()

    if not portfolio["session"]["active"]:
        print("[midday] No active session. Exiting.")
        return

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

    send_message("\n".join(lines))
    print(f"[midday] Alert sent. {len(alerts)} action items, {len(normal)} monitoring.")


if __name__ == "__main__":
    main()
