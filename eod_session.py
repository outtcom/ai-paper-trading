"""
End-of-day session runner.
Triggered by GitHub Actions at 4:15 PM ET on weekdays.

Flow:
  1. Check all open positions against live closing prices
  2. Take partial profit (50%) when price reaches 1:1 R/R — move SL to breakeven
  3. Update trailing stops for positions that made new highs today
  4. Close any positions where TP or SL was triggered
  5. Close positions held 3+ days with flat P&L (dead money rule)
  6. Calculate total portfolio equity
  7. Update SPY benchmark and Sharpe ratio
  8. Record equity snapshot on the equity curve
  9. Send EOD summary to Telegram
  10. Advance the session day counter

Usage:
  python eod_session.py
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.market_data import get_latest_price
from tools.session_manager import (
    advance_day,
    close_position,
    get_portfolio,
    get_session_day,
    partial_close_position,
    record_equity,
    update_spy_benchmark,
    update_trailing_stop,
)
from tools.telegram_bot import send_message

DEAD_MONEY_DAYS = 3      # close position if held this many days with no progress
DEAD_MONEY_BAND = 0.01   # "flat" = within ±1% of entry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _days_held(pos: dict) -> int:
    """Return how many calendar days the position has been open."""
    try:
        opened = datetime.strptime(pos["opened_date"], "%Y-%m-%d")
        return (datetime.utcnow() - opened).days
    except Exception:
        return 0


def _check_partial_profit(portfolio: dict) -> list:
    """
    At 1:1 R/R (halfway to TP), sell 50% of each position and move SL to breakeven.
    Only fires once per position (partial_taken flag).
    """
    partials = []
    for ticker, pos in list(portfolio["positions"].items()):
        if pos.get("partial_taken"):
            continue
        partial_price = pos.get("partial_profit_price")
        if not partial_price:
            # Derive from entry + sl_pct if missing (backward compat)
            sl_pct = pos.get("stop_loss_pct", 3) / 100
            partial_price = round(pos["entry_price"] * (1 + sl_pct), 2)

        try:
            price = get_latest_price(ticker)
            if price >= partial_price:
                qty_to_close = max(1, pos["qty"] // 2)
                print(f"[eod] PARTIAL PROFIT: {ticker} @ ${price:.2f} (1:1 level ${partial_price:.2f})")
                trade = partial_close_position(ticker, qty_to_close, price)
                trade["current_price"] = price
                partials.append(trade)
        except Exception as e:
            print(f"[eod] Partial profit check error for {ticker}: {e}")

    return partials


def _update_trailing_stops(portfolio: dict) -> list:
    """
    For each open position, ratchet the stop loss upward if price made a new high.
    Returns list of tickers where SL was updated.
    """
    updated = []
    for ticker in list(portfolio["positions"].keys()):
        try:
            price = get_latest_price(ticker)
            if update_trailing_stop(ticker, price):
                updated.append((ticker, price))
        except Exception as e:
            print(f"[eod] Trailing stop error for {ticker}: {e}")
    return updated


def _check_tp_sl(portfolio: dict) -> list:
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


def _check_time_exits(portfolio: dict) -> list:
    """
    Dead money rule: close positions held 3+ days that are within ±1% of entry.
    These are tying up capital with no thesis follow-through.
    """
    exits = []
    for ticker, pos in list(portfolio["positions"].items()):
        days = _days_held(pos)
        if days < DEAD_MONEY_DAYS:
            continue
        try:
            price = get_latest_price(ticker)
            entry = pos["entry_price"]
            pct_from_entry = abs(price - entry) / entry
            if pct_from_entry <= DEAD_MONEY_BAND:
                print(f"[eod] DEAD MONEY EXIT: {ticker} held {days}d, flat at {pct_from_entry*100:.1f}% from entry")
                trade = close_position(ticker, price, "time_exit")
                trade["current_price"] = price
                trade["days_held"] = days
                exits.append(trade)
        except Exception as e:
            print(f"[eod] Time exit check error for {ticker}: {e}")

    return exits


def _total_equity(portfolio: dict) -> float:
    """Cash + mark-to-market value of all open positions."""
    equity = portfolio["cash"]
    for ticker, pos in portfolio["positions"].items():
        try:
            price = get_latest_price(ticker)
            equity += price * pos["qty"]
        except Exception:
            equity += pos["cost_basis"]
    return round(equity, 2)


def _build_eod_message(
    portfolio: dict,
    closed_trades: list,
    partial_trades: list,
    time_exits: list,
    trailing_updates: list,
    equity: float,
    session_day: int,
    total_days: int,
) -> str:
    initial = portfolio["initial_capital"]
    total_return = round((equity - initial) / initial * 100, 2)
    days_remaining = total_days - session_day
    return_sign = "+" if total_return >= 0 else ""

    lines = [f"📋 <b>EOD SUMMARY — Day {session_day}/{total_days}</b>\n"]

    # Partial profit notifications
    for trade in partial_trades:
        sign = "+" if trade["pnl"] >= 0 else ""
        lines.append(
            f"💰 <b>PARTIAL PROFIT — {trade['ticker']}</b>\n"
            f"Sold {trade['qty']} shares @ ${trade['exit_price']:.2f}\n"
            f"Locked: {sign}${trade['pnl']:.2f} ({sign}{trade['pnl_pct']:.1f}%)  "
            f"<i>SL moved to breakeven</i>\n"
        )

    # Trailing stop updates
    if trailing_updates:
        lines.append("<b>Trailing Stops Updated:</b>")
        for ticker, price in trailing_updates:
            pos = portfolio["positions"].get(ticker, {})
            new_sl = pos.get("stop_loss", "?")
            lines.append(f"  ↑ {ticker}: SL raised → ${new_sl:.2f} (high ${price:.2f})")
        lines.append("")

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

    # Dead money exits
    for trade in time_exits:
        sign = "+" if trade["pnl"] >= 0 else ""
        lines.append(
            f"⏳ <b>TIME EXIT ({trade.get('days_held', '?')}d) — {trade['ticker']}</b>\n"
            f"Entry: ${trade['entry_price']:.2f} → Exit: ${trade['exit_price']:.2f}\n"
            f"P&amp;L: {sign}${trade['pnl']:.2f} ({sign}{trade['pnl_pct']:.1f}%)  "
            f"<i>No follow-through — capital recycled</i>\n"
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
                partial_note = " (partial taken)" if pos.get("partial_taken") else ""
                lines.append(
                    f"  {ticker}: ${pos['entry_price']:.2f} → ${price:.2f}  "
                    f"({sign}{unr_pct:.1f}%)  "
                    f"TP ${pos['take_profit']:.2f}  SL ${pos['stop_loss']:.2f}{partial_note}"
                )
            except Exception as e:
                lines.append(f"  {ticker}: (price unavailable: {e})")
        lines.append("")

    # No activity today
    all_closed = closed_trades + partial_trades + time_exits
    if not all_closed and not open_pos:
        lines.append("<i>No open positions today.</i>\n")

    # Session stats
    completed = portfolio.get("trade_history", [])
    winners = [t for t in completed if t.get("pnl", 0) > 0]
    win_rate = f"{len(winners)}/{len(completed)}" if completed else "0/0"

    # Sharpe ratio
    sharpe = portfolio.get("stats", {}).get("sharpe")
    sharpe_str = f"{sharpe:.2f}" if sharpe is not None else "N/A (need more data)"

    # SPY benchmark
    spy_ret = portfolio.get("stats", {}).get("benchmark_return_pct")
    spy_str = f"{'+' if spy_ret and spy_ret >= 0 else ''}{spy_ret:.1f}%" if spy_ret is not None else "N/A"
    our_ret_str = f"{return_sign}{total_return:.1f}%"

    lines.append(
        f"Session Equity:  <b>${equity:,.2f}</b> ({return_sign}{total_return:.1f}%)\n"
        f"vs SPY:          {our_ret_str} vs {spy_str} (benchmark)\n"
        f"Sharpe Ratio:    {sharpe_str}\n"
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

    # Step 1: Partial profit at 1:1 R/R (before checking full TP/SL)
    partial_trades = _check_partial_profit(portfolio)
    portfolio = get_portfolio()  # reload after partials

    # Step 2: Update trailing stops
    trailing_updates = _update_trailing_stops(portfolio)
    portfolio = get_portfolio()  # reload after trailing stop updates

    # Step 3: Check for full TP/SL hits
    closed_trades = _check_tp_sl(portfolio)
    portfolio = get_portfolio()  # reload after closes

    # Step 4: Time-based exits (dead money rule)
    time_exits = _check_time_exits(portfolio)
    portfolio = get_portfolio()  # reload after time exits

    # Step 5: Update SPY benchmark
    try:
        spy_price = get_latest_price("SPY")
        update_spy_benchmark(spy_price)
        print(f"[eod] SPY @ ${spy_price:.2f}")
    except Exception as e:
        print(f"[eod] Could not fetch SPY price: {e}")

    # Step 6: Calculate and record equity
    portfolio = get_portfolio()
    equity = _total_equity(portfolio)
    record_equity(equity)

    # Reload one more time for the message (equity_curve + stats updated)
    portfolio = get_portfolio()

    # Step 7: Build and send EOD summary
    msg = _build_eod_message(
        portfolio, closed_trades, partial_trades, time_exits,
        trailing_updates, equity, session_day, total_days,
    )
    send_message(msg)
    print(f"[eod] Summary sent. Equity: ${equity:,.2f}")

    # Step 8: Advance the session day counter
    new_day = advance_day()
    print(f"[eod] Advanced to day {new_day}.")

    if new_day > total_days:
        # Final session summary
        final_portfolio = get_portfolio()
        initial = final_portfolio["initial_capital"]
        final_ret = round((equity - initial) / initial * 100, 2)
        sharpe = final_portfolio.get("stats", {}).get("sharpe")
        spy_ret = final_portfolio.get("stats", {}).get("benchmark_return_pct")
        completed = final_portfolio.get("trade_history", [])
        winners = [t for t in completed if t.get("pnl", 0) > 0]

        send_message(
            f"🏁 <b>PAPER TRADING SESSION COMPLETE</b>\n\n"
            f"Final Equity: <b>${equity:,.2f}</b>\n"
            f"Total Return: <b>{'+' if final_ret >= 0 else ''}{final_ret:.1f}%</b>\n"
            f"vs SPY:       {'+' if spy_ret and spy_ret >= 0 else ''}{spy_ret:.1f}%" if spy_ret else "" +
            f"\nSharpe Ratio: {f'{sharpe:.2f}' if sharpe else 'N/A'}\n"
            f"Win Rate:     {len(winners)}/{len(completed)} trades\n\n"
            f"Check the dashboard for full details."
        )
        print("[eod] Session complete!")


if __name__ == "__main__":
    main()
