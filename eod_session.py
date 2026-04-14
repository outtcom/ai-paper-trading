"""
End-of-day session runner.
Triggered by GitHub Actions at 4:15 PM ET on weekdays.

Flow:
  1. Check all open positions against live closing prices
  2. Close any positions where TP or SL was triggered
  3. Calculate total portfolio equity
  4. Record equity snapshot on the equity curve
  5. Send EOD summary to Telegram
  6. Advance the session day counter (marks session done after day 10)

Usage:
  python eod_session.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.market_data import get_latest_price
from tools.session_manager import (
    advance_day,
    close_position,
    get_portfolio,
    get_session_day,
    record_equity,
)
from tools.telegram_bot import send_message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_tp_sl(portfolio: dict) -> list[dict]:
    """
    Compare each open position's TP/SL against the current market price.
    Closes the position and returns a list of trade records for any that triggered.
    """
    closed = []
    for ticker, pos in list(portfolio["positions"].items()):
        try:
            price = get_latest_price(ticker)
            tp = pos["take_profit"]
            sl = pos["stop_loss"]

            if price >= tp:
                print(f"[eod] TP HIT: {ticker}  price=${price:.2f}  TP=${tp:.2f}")
                trade = close_position(ticker, tp, "take_profit")
                trade["current_price"] = price
                closed.append(trade)
            elif price <= sl:
                print(f"[eod] SL HIT: {ticker}  price=${price:.2f}  SL=${sl:.2f}")
                trade = close_position(ticker, sl, "stop_loss")
                trade["current_price"] = price
                closed.append(trade)
            else:
                print(f"[eod] {ticker}  price=${price:.2f}  TP=${tp:.2f}  SL=${sl:.2f}  (holding)")

        except Exception as e:
            print(f"[eod] Error checking {ticker}: {e}")

    return closed


def _total_equity(portfolio: dict) -> float:
    """Cash + mark-to-market value of all open positions."""
    equity = portfolio["cash"]
    for ticker, pos in portfolio["positions"].items():
        try:
            price = get_latest_price(ticker)
            equity += price * pos["qty"]
        except Exception:
            # Fall back to cost basis if price fetch fails
            equity += pos["cost_basis"]
    return round(equity, 2)


def _build_eod_message(
    portfolio: dict,
    closed_trades: list,
    equity: float,
    session_day: int,
    total_days: int,
) -> str:
    initial = portfolio["initial_capital"]
    total_return = round((equity - initial) / initial * 100, 2)
    days_remaining = total_days - session_day
    return_sign = "+" if total_return >= 0 else ""

    lines = [f"📋 <b>EOD SUMMARY — Day {session_day}/{total_days}</b>\n"]

    # TP/SL closures today
    for trade in closed_trades:
        if trade["reason"] == "take_profit":
            emoji, label = "🎯", "TAKE PROFIT"
        else:
            emoji, label = "🛑", "STOP LOSS"
        sign = "+" if trade["pnl"] >= 0 else ""
        lines.append(
            f"{emoji} <b>{label} — {trade['ticker']}</b>\n"
            f"Entry: ${trade['entry_price']:.2f} → Exit: ${trade['exit_price']:.2f}\n"
            f"P&amp;L: {sign}${trade['pnl']:.2f} ({sign}{trade['pnl_pct']:.1f}%)\n"
        )

    # Open positions still held
    open_pos = portfolio.get("positions", {})
    if open_pos:
        lines.append("<b>Open Positions:</b>")
        for ticker, pos in open_pos.items():
            try:
                price = get_latest_price(ticker)
                unr = round((price - pos["entry_price"]) * pos["qty"], 2)
                unr_pct = round((price - pos["entry_price"]) / pos["entry_price"] * 100, 2)
                sign = "+" if unr >= 0 else ""
                lines.append(
                    f"  {ticker}: ${pos['entry_price']:.2f} → ${price:.2f}  "
                    f"({sign}{unr_pct:.1f}%)  "
                    f"TP ${pos['take_profit']:.2f}  SL ${pos['stop_loss']:.2f}"
                )
            except Exception as e:
                lines.append(f"  {ticker}: (price unavailable: {e})")
        lines.append("")

    # No activity today
    if not closed_trades and not open_pos:
        lines.append("<i>No open positions today.</i>\n")

    # Session stats
    completed = portfolio.get("trade_history", [])
    winners = [t for t in completed if t.get("pnl", 0) > 0]
    win_rate = f"{len(winners)}/{len(completed)}" if completed else "0/0"

    lines.append(
        f"Session Equity:  <b>${equity:,.2f}</b> ({return_sign}{total_return:.1f}%)\n"
        f"Win Rate:        {win_rate} closed trades\n"
        f"Days Remaining:  {days_remaining}"
    )

    if days_remaining == 0:
        lines.append("\n🏁 <b>Session complete! Final results above.</b>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    today = datetime.today().strftime("%Y-%m-%d")
    print(f"\n[eod] ========== EOD Session {today} ==========")

    portfolio = get_portfolio()

    if not portfolio["session"]["active"]:
        print("[eod] No active session. Exiting.")
        return

    session_day = get_session_day()
    total_days = portfolio["session"]["total_days"]
    print(f"[eod] Day {session_day}/{total_days}")

    # Check for TP/SL hits and close positions
    closed_trades = _check_tp_sl(portfolio)

    # Reload after any closes
    portfolio = get_portfolio()

    # Calculate and record equity
    equity = _total_equity(portfolio)
    record_equity(equity)

    # Reload one more time for the message (equity_curve updated)
    portfolio = get_portfolio()

    # Build and send EOD summary
    msg = _build_eod_message(portfolio, closed_trades, equity, session_day, total_days)
    send_message(msg)
    print(f"[eod] Summary sent. Equity: ${equity:,.2f}")

    # Advance the session day counter
    new_day = advance_day()
    print(f"[eod] Advanced to day {new_day}.")

    if new_day > total_days:
        send_message(
            "🏁 <b>Paper trading session complete!</b>\n\n"
            "All 10 days are done. Check the dashboard for your final results."
        )
        print("[eod] Session complete!")


if __name__ == "__main__":
    main()
