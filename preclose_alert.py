"""
Pre-close alert — runs at 3:30 PM ET.
Triggered by GitHub Actions daily.

Sends a quick Telegram heads-up showing:
  - All open positions: current price, unrealized P&L, distance to TP/SL
  - A flag if any position is dangerously close to its SL (within 0.5%)

This gives 30 min to mentally prepare before the 4:00 PM close.
"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.market_data import get_latest_price
from tools.session_manager import get_portfolio, get_session_day
from tools.telegram_bot import broadcast_message


def main():
    print(f"\n[preclose] ========== Pre-Close Alert {datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d')} ==========")

    portfolio = get_portfolio()

    if not portfolio["session"]["active"]:
        print("[preclose] No active session. Exiting.")
        return

    positions = portfolio.get("positions", {})

    if not positions:
        print("[preclose] No open positions. Skipping alert.")
        return

    session_day = get_session_day()
    total_days  = portfolio["session"]["total_days"]
    lines       = [f"⏰ <b>PRE-CLOSE ALERT — Day {session_day}/{total_days}</b> (30 min to close)\n"]
    danger      = []

    for ticker, pos in positions.items():
        try:
            price      = get_latest_price(ticker)
            entry      = pos["entry_price"]
            tp         = pos["take_profit"]
            sl         = pos["stop_loss"]
            qty        = pos["qty"]

            unr        = round((price - entry) * qty, 2)
            unr_pct    = round((price - entry) / entry * 100, 2)
            pct_to_tp  = round((tp - price) / price * 100, 2)
            pct_to_sl  = round((price - sl) / price * 100, 2)

            sign = "+" if unr >= 0 else ""
            emoji = "📈" if unr >= 0 else "📉"

            # Guard against negative distances (price already past TP or SL)
            tp_label = f"+{pct_to_tp:.1f}% away" if pct_to_tp >= 0 else "⚠️ ABOVE TP"
            sl_label = f"-{pct_to_sl:.1f}% away" if pct_to_sl >= 0 else "🛑 BELOW SL"

            lines.append(
                f"{emoji} <b>{ticker}</b>\n"
                f"  Price: ${price:.2f}  (entry ${entry:.2f})\n"
                f"  P&amp;L: {sign}${unr:.2f} ({sign}{unr_pct:.1f}%)\n"
                f"  TP: ${tp:.2f}  ({tp_label})\n"
                f"  SL: ${sl:.2f}  ({sl_label})"
            )

            # Flag if within 0.5% of stop loss (or already past it)
            if pct_to_sl <= 0.5:
                danger.append(f"🚨 {ticker} is {abs(pct_to_sl):.2f}% from stop loss!")

        except Exception as e:
            lines.append(f"⚠️ <b>{ticker}</b>: price unavailable ({e})")

    if danger:
        lines.append("\n" + "\n".join(danger))

    lines.append("\n<i>Next update: EOD summary at 4:15 PM ET.</i>")

    broadcast_message("\n".join(lines))
    print("[preclose] Alert sent.")


if __name__ == "__main__":
    main()
