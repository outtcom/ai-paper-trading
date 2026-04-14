"""
Pre-market gap scanner — runs at 7:00 AM ET (30 min before morning session).
Triggered by GitHub Actions daily.

Scans all watchlist tickers for significant pre-market gaps vs prior close.
A gap > 2% means the overnight thesis may have changed — flags it BEFORE
the morning session so the approval card reflects the new reality.

Sends a Telegram alert only if at least one ticker has gapped significantly.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import WATCHLIST
from tools.market_regime import get_premarket_gaps, is_event_blocked
from tools.session_manager import get_portfolio
from tools.telegram_bot import send_message

GAP_THRESHOLD = 2.0   # percent — flag if gap exceeds this


def main():
    today = datetime.today().strftime("%Y-%m-%d")
    print(f"\n[premarket] ========== Pre-Market Gap Scanner {today} ==========")

    portfolio = get_portfolio()
    if not portfolio["session"]["active"]:
        print("[premarket] No active session. Skipping.")
        return

    # Check if a macro event will block today's trading anyway
    event_blocked, event_reason = is_event_blocked(today)
    if event_blocked:
        send_message(
            f"📅 <b>Pre-Market Alert — {today}</b>\n\n"
            f"⚠️ Today's morning session will be blocked:\n"
            f"🚫 {event_reason}\n\n"
            f"<i>No trade analysis will run today. Staying in cash.</i>"
        )
        print(f"[premarket] Event blocked today: {event_reason}")
        return

    gaps = get_premarket_gaps(WATCHLIST, gap_threshold=GAP_THRESHOLD / 100)

    flagged = {t: g for t, g in gaps.items() if g.get("flagged")}
    unflagged = {t: g for t, g in gaps.items() if not g.get("flagged") and "gap_pct" in g}

    if not flagged and not unflagged:
        print("[premarket] No gap data available. Skipping alert.")
        return

    lines = [f"📡 <b>PRE-MARKET SCAN — {today}</b> (30 min to open)\n"]

    if flagged:
        lines.append(f"⚠️ <b>Significant Gaps (&gt;{GAP_THRESHOLD}%):</b>")
        for ticker, data in sorted(flagged.items(), key=lambda x: abs(x[1].get("gap_pct", 0)), reverse=True):
            pct = data.get("gap_pct", 0)
            prev = data.get("prev_close", 0)
            curr = data.get("current", 0)
            direction = "⬆️" if pct > 0 else "⬇️"
            sign = "+" if pct > 0 else ""
            lines.append(
                f"  {direction} <b>{ticker}</b>: {sign}{pct:.1f}%  "
                f"(prev close ${prev:.2f} → now ${curr:.2f})"
            )
        lines.append(
            f"\n<i>⚠️ Gapped tickers may have stale AI thesis. "
            f"The morning session will recalculate — approve carefully.</i>\n"
        )
    else:
        lines.append(f"✅ <b>No significant gaps</b> (all within ±{GAP_THRESHOLD}%)\n")

    # Show all tickers for context
    if unflagged:
        lines.append("<b>All other tickers:</b>")
        for ticker, data in unflagged.items():
            pct = data.get("gap_pct", 0)
            sign = "+" if pct > 0 else ""
            lines.append(f"  {ticker}: {sign}{pct:.1f}%")

    session_day = portfolio["session"].get("current_day", "?")
    total_days = portfolio["session"].get("total_days", 10)
    equity = portfolio.get("equity", portfolio["initial_capital"])
    lines.append(f"\n<i>Day {session_day}/{total_days}  |  Equity ${equity:,.2f}</i>")
    lines.append("<i>Morning session analysis starts in 30 min.</i>")

    msg = "\n".join(lines)
    send_message(msg)
    print(f"[premarket] Scan sent. {len(flagged)} tickers flagged.")


if __name__ == "__main__":
    main()
