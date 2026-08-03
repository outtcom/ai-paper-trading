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
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.market_data import get_latest_price, _yahoo_direct_ohlcv
from tools.session_manager import (
    advance_day,
    already_ran_today,
    close_position,
    close_midcap_signal,
    close_penny_signal,
    get_open_midcap_signals,
    get_open_penny_signals,
    get_portfolio,
    get_session_day,
    mark_ran_today,
    open_position,
    scale_into_position,
    partial_close_position,
    record_equity,
    record_spy_equity,
    record_midcap_equity,
    record_penny_equity,
    update_benchmark_indices,
    update_last_price,
    update_spy_benchmark,
    update_trailing_stop,
)
from tools.telegram_bot import broadcast_message, send_private_only
from tools.agent_tracker import update_portfolio_tracker, format_ic_report
from config import TURNAROUND_FIX_DATE, PHASE4_EVIDENCE_TRADE_COUNT

DEAD_MONEY_DAYS      = 10    # close position if held this many TRADING days with no progress
DEAD_MONEY_FLOOR      = -0.02  # below this, it's a loser for the stop-loss to handle, not dead money
DEAD_MONEY_THRESHOLD = 0.02  # "not working" = gain < +2% after DEAD_MONEY_DAYS trading days


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _days_held(pos: dict, current_session_day: int) -> int:
    """
    Return how many TRADING days the position has been open, using the session
    day counter (incremented once per EOD run, i.e. once per trading day this
    pipeline actually ran) rather than raw calendar days — a 3-calendar-day
    weekend gap should not count as 3 days toward the dead-money clock.
    Falls back to calendar-day math for legacy positions opened before
    opened_session_day was tracked.
    """
    opened_day = pos.get("opened_session_day")
    if opened_day is not None:
        return max(0, current_session_day - opened_day)
    try:
        opened = datetime.strptime(pos["opened_date"], "%Y-%m-%d")
        return (datetime.now(timezone.utc).replace(tzinfo=None) - opened).days
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
        direction     = pos.get("direction", "long")
        partial_price = pos.get("partial_profit_price")
        if not partial_price:
            sl_pct = pos.get("stop_loss_pct", 3) / 100
            entry  = pos["entry_price"]
            partial_price = round(entry * (1 - sl_pct) if direction == "short" else entry * (1 + sl_pct), 2)

        try:
            price = get_latest_price(ticker)
            triggered = (price <= partial_price) if direction == "short" else (price >= partial_price)
            if triggered:
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
            price     = get_latest_price(ticker)
            tp        = pos.get("take_profit")
            sl        = pos.get("stop_loss")
            if tp is None or sl is None:
                print(f"[eod] {ticker}: no TP/SL stored — holding (legacy position)")
                continue
            direction = pos.get("direction", "long")

            if direction == "short":
                tp_hit = price <= tp   # price fell to target
                sl_hit = price >= sl   # price rose past stop
            else:
                tp_hit = price >= tp
                sl_hit = price <= sl

            if tp_hit:
                print(f"[eod] TP HIT: {ticker}  price=${price:.2f}  TP=${tp:.2f}")
                trade = close_position(ticker, tp, "take_profit")
                trade["current_price"] = price
                closed.append(trade)
            elif sl_hit:
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
    Dead money rule: close positions held DEAD_MONEY_DAYS+ trading days that are
    flat-to-slightly-up (DEAD_MONEY_FLOOR < pnl_pct < DEAD_MONEY_THRESHOLD).
    Catches genuinely stagnant positions (not moving, or tiny-gain AMZN-style
    18-day +0.7%) without preempting the stop-loss: real losers below the floor
    are a risk decision for the stop-loss to make, not an arbitrary clock.
    """
    current_session_day = get_session_day()
    exits = []
    for ticker, pos in list(portfolio["positions"].items()):
        days = _days_held(pos, current_session_day)
        if days < DEAD_MONEY_DAYS:
            continue
        try:
            price     = get_latest_price(ticker)
            entry     = pos["entry_price"]
            direction = pos.get("direction", "long")
            pnl_pct   = (price - entry) / entry if direction == "long" else (entry - price) / entry

            if DEAD_MONEY_FLOOR < pnl_pct < DEAD_MONEY_THRESHOLD:
                print(f"[eod] DEAD MONEY EXIT: {ticker} held {days}td, only {pnl_pct*100:+.1f}% — capital reassigned")
                trade = close_position(ticker, price, "time_exit")
                trade["current_price"] = price
                trade["days_held"] = days
                exits.append(trade)
        except Exception as e:
            print(f"[eod] Time exit check error for {ticker}: {e}")

    return exits


def _scale_into_winners(portfolio: dict) -> list:
    """
    Scale 50% into positions that are +10%+ and still below partial-profit trigger.
    Only fires if: (a) cash headroom exists, (b) partial not yet taken,
    (c) position hasn't been scaled before, (d) NOT already at max position cap.
    Returns list of (ticker, add_qty, price) tuples executed.
    """
    SCALE_TRIGGER  = 0.10    # scale in when +10% gain confirmed
    MAX_SCALE_PCT  = 0.15    # cap add-on at 15% of current cash (conservative)
    MIN_CASH_RATIO = 0.20    # need at least 20% cash remaining after add

    scaled = []
    cash   = portfolio.get("cash", 0)
    equity = portfolio.get("equity", cash or 1)

    for ticker, pos in list(portfolio["positions"].items()):
        if pos.get("partial_taken"):
            continue   # already took profit — don't add more
        if pos.get("scale_taken"):
            continue   # already scaled into this position

        direction = pos.get("direction", "long")
        entry     = pos["entry_price"]
        orig_qty  = pos["qty"]

        try:
            price   = get_latest_price(ticker)
            pnl_pct = (price - entry) / entry if direction == "long" else (entry - price) / entry

            if pnl_pct < SCALE_TRIGGER:
                continue

            # How many shares to add (50% of original qty, minimum 1)
            add_qty = max(1, orig_qty // 2)
            cost    = add_qty * price

            # Capital checks
            if cost > cash * MAX_SCALE_PCT:
                add_qty = max(1, int(cash * MAX_SCALE_PCT / price))
                cost    = add_qty * price
            if cost > cash or (cash - cost) / equity < MIN_CASH_RATIO:
                print(f"[eod] SCALE {ticker}: insufficient cash (need ${cost:.0f}, have ${cash:.0f})")
                continue

            print(f"[eod] SCALE INTO {ticker}: adding {add_qty}sh @ ${price:.2f} "
                  f"(+{pnl_pct:.1%} gain, orig {orig_qty}sh @ ${entry:.2f})")
            scale_into_position(
                ticker       = ticker,
                add_qty      = add_qty,
                price        = price,
                journal_note = f"Scale-in: +{add_qty}sh at +{pnl_pct:.1%} momentum confirmation",
            )
            cash -= cost
            scaled.append({"ticker": ticker, "add_qty": add_qty, "price": price, "pnl_pct": pnl_pct})

        except Exception as e:
            print(f"[eod] Scale-in error for {ticker}: {e}")

    return scaled


def _agent_line(journal: list, ticker: str) -> str:
    """
    Return a formatted agent attribution line for the given ticker's BUY journal entry.
    Returns "" if no agent_signals exist (legacy trades or trades without attribution).
    Note: matches the FIRST BUY entry for this ticker — safe because the system
    holds at most one position per ticker at a time.
    """
    trade_journal = next(
        (j for j in journal if j.get("ticker") == ticker and j.get("action") == "BUY"),
        {}
    )
    signals = trade_journal.get("agent_signals", {})
    if not signals:
        return ""
    aligned = []
    if any(k in str(signals.get("fundamental", "")).lower() for k in ("buy", "bullish")):
        aligned.append("Fund ✓")
    if any(k in str(signals.get("technical", "")).lower() for k in ("bullish", "buy")):
        aligned.append("Tech ✓")
    if any(k in str(signals.get("sentiment", "")).lower() for k in ("positive", "bullish")):
        aligned.append("Sent ✓")
    if signals.get("risk_approved"):
        aligned.append("Risk ✓")
    return f"Agents: {', '.join(aligned)}\n" if aligned else ""


def _total_equity(portfolio: dict) -> float:
    """Cash + mark-to-market value of all open positions. Also persists last_price.
    For short positions: cash already includes short proceeds; subtract cover cost."""
    equity = portfolio["cash"]
    for ticker, pos in portfolio["positions"].items():
        direction = pos.get("direction", "long")
        try:
            price = get_latest_price(ticker)
            update_last_price(ticker, price)
            if direction == "short":
                equity -= price * pos["qty"]   # subtract current cover cost
            else:
                equity += price * pos["qty"]
        except Exception:
            if direction == "short":
                equity -= pos.get("cost_basis", 0)
            else:
                equity += pos.get("cost_basis", 0)
    return round(equity, 2)


def _reconcile_equity(portfolio: dict, equity: float) -> None:
    """
    Assert equity == initial_capital + realized P&L + unrealized P&L, within $1.
    Catches silent ledger corruption (e.g. a position overwrite that drains cash
    without a matching trade_history entry) the same day it happens instead of
    letting it run for days undetected.
    """
    initial = portfolio.get("initial_capital", 0)
    realized = sum(t.get("pnl", 0) for t in portfolio.get("trade_history", []))

    unrealized = 0.0
    for ticker, pos in portfolio.get("positions", {}).items():
        price = pos.get("last_price", pos.get("entry_price", 0))
        direction = pos.get("direction", "long")
        if direction == "short":
            unrealized += (pos.get("entry_price", price) - price) * pos.get("qty", 0)
        else:
            unrealized += (price - pos.get("entry_price", price)) * pos.get("qty", 0)

    expected = round(initial + realized + unrealized, 2)
    discrepancy = round(equity - expected, 2)

    if abs(discrepancy) >= 1.0:
        msg = (
            f"🚨 <b>EQUITY RECONCILIATION FAILED</b>\n\n"
            f"Reported equity: ${equity:,.2f}\n"
            f"Expected (initial + realized + unrealized): ${expected:,.2f}\n"
            f"Discrepancy: ${discrepancy:+,.2f}\n\n"
            f"This means cash/positions moved without a matching trade_history entry — "
            f"likely a ledger bug, not a trading loss. Investigate before trusting today's "
            f"equity, stats, or strategy brief."
        )
        print(f"[eod] RECONCILIATION FAILED: discrepancy ${discrepancy:+,.2f}")
        send_private_only(msg)
    else:
        print(f"[eod] Reconciliation OK: equity ${equity:,.2f} vs expected ${expected:,.2f} (diff ${discrepancy:+,.2f})")


def _check_phase4_evidence(portfolio: dict) -> bool:
    """
    One-time Telegram alert once enough trades opened under the post-turnaround-plan
    rules (Phase 0-3, shipped TURNAROUND_FIX_DATE) have closed to give a real read on
    whether the Phase 4 structural decision (systematic candidates + LLM-as-veto) is
    worth pursuing, instead of guessing from backtest alone. Fires once; returns True
    if it fired (so the caller knows to persist the flag).
    """
    if portfolio.get("phase4_checkpoint_sent"):
        return False

    post_fix = [
        t for t in portfolio.get("trade_history", [])
        if t.get("opened_date", "") >= TURNAROUND_FIX_DATE
    ]
    if len(post_fix) < PHASE4_EVIDENCE_TRADE_COUNT:
        return False

    wins = [t for t in post_fix if t.get("pnl", 0) > 0]
    win_rate = len(wins) / len(post_fix) * 100
    avg_pnl_pct = sum(t.get("pnl_pct", 0) for t in post_fix) / len(post_fix)
    total_pnl = sum(t.get("pnl", 0) for t in post_fix)

    send_private_only(
        f"📊 <b>Phase 4 Evidence Checkpoint</b>\n\n"
        f"{len(post_fix)} trades have closed since the turnaround-plan fix "
        f"({TURNAROUND_FIX_DATE}) — enough for a first meaningful read.\n\n"
        f"Win rate: {win_rate:.0f}%\n"
        f"Avg P&L: {avg_pnl_pct:+.2f}%\n"
        f"Total P&L: ${total_pnl:+,.2f}\n\n"
        f"Time to revisit the Phase 4 structural decision (systematic candidate "
        f"generation + LLM-as-veto) with real post-fix evidence instead of backtest "
        f"alone. See CLAUDE.md turnaround plan notes."
    )
    print(f"[eod] Phase 4 evidence checkpoint fired — {len(post_fix)} post-fix trades closed.")
    portfolio["phase4_checkpoint_sent"] = True
    return True


def _resolve_signals_generic(today: str, open_signals: list, close_fn, label: str) -> list:
    """
    Shared resolution logic for mid-cap and penny signal pools.
    Checks daily OHLCV to see if TP or SL was hit; exits at close otherwise.
    Auto-closes signals whose auto_close_date has passed.
    """
    from datetime import timedelta
    resolved = []
    for signal in open_signals:
        if signal.get("auto_close_date", "9999-99-99") > today:
            continue
        try:
            ticker    = signal["ticker"]
            gen_date  = signal.get("generated_date", today)
            direction = signal.get("direction", "long")
            tp        = signal["target_price"]
            sl        = signal["stop_price"]

            bars = _yahoo_direct_ohlcv(ticker, gen_date, today)

            # Scan bars chronologically from gen_date for first TP/SL hit
            exit_price = None
            for bar in sorted(bars, key=lambda b: b.get("date", "")):
                if bar.get("date", "") < gen_date:
                    continue
                if direction == "long":
                    if bar["high"] >= tp:
                        exit_price = tp; break
                    elif bar["low"] <= sl:
                        exit_price = sl; break
                else:
                    if bar["low"] <= tp:
                        exit_price = tp; break
                    elif bar["high"] >= sl:
                        exit_price = sl; break

            if exit_price is None:
                exit_price = bars[-1]["close"] if bars else get_latest_price(ticker)

            closed = close_fn(signal["id"], exit_price, today)
            if closed:
                resolved.append(closed)
                print(f"[eod] {label} resolved: {ticker} {closed.get('outcome')} "
                      f"{closed.get('pnl_pct', 0):+.2f}% (${closed.get('pnl_usd', 0):+.2f})")
        except Exception as e:
            print(f"[eod] Error resolving {label} {signal.get('id')}: {e}")
    return resolved


def _resolve_midcap_signals(today: str) -> list:
    return _resolve_signals_generic(today, get_open_midcap_signals(), close_midcap_signal, "Midcap")


def _resolve_penny_signals(today: str) -> list:
    return _resolve_signals_generic(today, get_open_penny_signals(), close_penny_signal, "Penny")


def _build_eod_message(
    portfolio: dict,
    closed_trades: list,
    partial_trades: list,
    time_exits: list,
    trailing_updates: list,
    equity: float,
    session_day: int,
    total_days: int,
    resolved_signals: list = None,
    scaled_positions: list = None,
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
            new_sl = pos.get("stop_loss")
            if new_sl is not None:
                lines.append(f"  ↑ {ticker}: SL raised → ${float(new_sl):.2f} (high ${float(price):.2f})")
            else:
                lines.append(f"  ↑ {ticker}: SL raised (closed same session) (high ${float(price):.2f})")
        lines.append("")

    # TP/SL closures today
    journal = portfolio.get("journal", [])
    for trade in closed_trades:
        if trade["reason"] == "take_profit":
            emoji, label = "🎯", "TAKE PROFIT"
        else:
            emoji, label = "🛑", "STOP LOSS"
        sign = "+" if trade["pnl"] >= 0 else ""

        # Agent attribution from journal entry
        agent_line = _agent_line(journal, trade["ticker"])

        lines.append(
            f"{emoji} <b>{label} — {trade['ticker']}</b>\n"
            f"Entry: ${trade['entry_price']:.2f} → Exit: ${trade['exit_price']:.2f}\n"
            f"P&amp;L: {sign}${trade['pnl']:.2f} ({sign}{trade['pnl_pct']:.1f}%)\n"
            + agent_line
        )

    # Scale-in notifications
    for s in (scaled_positions or []):
        lines.append(
            f"📈 <b>SCALED IN — {s['ticker']}</b>  +{s['add_qty']} shares @ ${s['price']:.2f}\n"
            f"Position up +{s['pnl_pct']*100:.1f}% — adding to winner\n"
        )

    # Dead money exits
    for trade in time_exits:
        sign = "+" if trade["pnl"] >= 0 else ""
        agent_line = _agent_line(journal, trade["ticker"])
        lines.append(
            f"⏳ <b>TIME EXIT ({trade.get('days_held', '?')}d) — {trade['ticker']}</b>\n"
            f"Entry: ${trade['entry_price']:.2f} → Exit: ${trade['exit_price']:.2f}\n"
            f"P&amp;L: {sign}${trade['pnl']:.2f} ({sign}{trade['pnl_pct']:.1f}%)  "
            f"<i>No follow-through — capital recycled</i>\n"
            + agent_line
        )

    # Open positions still held
    open_pos = portfolio.get("positions", {})
    if open_pos:
        lines.append("<b>Open Positions:</b>")
        for ticker, pos in open_pos.items():
            try:
                price     = get_latest_price(ticker)
                direction = pos.get("direction", "long")
                if direction == "short":
                    unr     = round((pos["entry_price"] - price) * pos["qty"], 2)
                    unr_pct = round((pos["entry_price"] - price) / pos["entry_price"] * 100, 2)
                    dir_tag = " 🔻"
                else:
                    unr     = round((price - pos["entry_price"]) * pos["qty"], 2)
                    unr_pct = round((price - pos["entry_price"]) / pos["entry_price"] * 100, 2)
                    dir_tag = ""
                sign = "+" if unr >= 0 else ""
                partial_note = " (partial taken)" if pos.get("partial_taken") else ""
                lines.append(
                    f"  {ticker}{dir_tag}: ${pos['entry_price']:.2f} → ${price:.2f}  "
                    f"({sign}{unr_pct:.1f}%)  "
                    f"TP ${pos['take_profit']:.2f}  SL ${pos['stop_loss']:.2f}{partial_note}"
                )
            except Exception as e:
                lines.append(f"  {ticker}: (price unavailable: {e})")
        lines.append("")

    # Mid-cap / penny signal resolutions
    if resolved_signals:
        lines.append("<b>Mid-Cap / Penny Signals Closed:</b>")
        for sig in resolved_signals:
            pct = sig.get("pnl_pct") or 0
            usd = sig.get("pnl_usd") or 0
            outcome = sig.get("outcome", "?").upper()
            emoji = "✅" if outcome == "WIN" else ("❌" if outcome == "LOSS" else "➖")
            sign  = "+" if pct >= 0 else ""
            lines.append(
                f"  {emoji} {sig['ticker']} ({sig.get('signal_type','?')}): "
                f"{sign}{pct:.2f}% ({sign}${usd:.2f}) → {outcome}"
            )
        mc  = portfolio.get("midcap_capital", {})
        pen = portfolio.get("penny_capital", {})
        lines.append(
            f"  Mid-cap pool: <b>${mc.get('equity', 5000):,.2f}</b>  |  "
            f"Penny pool: <b>${pen.get('equity', 5000):,.2f}</b>"
        )
        lines.append("")

    # No activity today
    all_closed = closed_trades + partial_trades + time_exits
    if not all_closed and not open_pos:
        lines.append("<i>No open positions today.</i>\n")

    # Session stats
    completed = portfolio.get("trade_history", [])
    winners = [t for t in completed if t.get("pnl", 0) > 0]
    win_rate = f"{len(winners)}/{len(completed)}" if completed else "0/0"

    stats = portfolio.get("stats", {})

    def _fmt_ratio(v):
        return f"{v:.2f}" if v is not None else "N/A"

    sharpe  = _fmt_ratio(stats.get("sharpe"))
    sortino = _fmt_ratio(stats.get("sortino"))
    calmar  = _fmt_ratio(stats.get("calmar"))
    max_dd  = f"{stats.get('max_drawdown_pct', 0):.1f}%" if stats.get("max_drawdown_pct") is not None else "N/A"

    spy_ret = stats.get("benchmark_return_pct")
    spy_str = f"{'+' if spy_ret and spy_ret >= 0 else ''}{spy_ret:.1f}%" if spy_ret is not None else "N/A"
    alpha   = round(total_return - spy_ret, 2) if spy_ret is not None else None
    alpha_str = f"{'+' if alpha and alpha >= 0 else ''}{alpha:.1f}%" if alpha is not None else "N/A"

    lines.append(
        f"Session Equity:  <b>${equity:,.2f}</b> ({return_sign}{total_return:.1f}%)\n"
        f"vs SPY:          {return_sign}{total_return:.1f}% vs {spy_str}  (alpha: {alpha_str})\n"
        f"Sharpe / Sortino / Calmar:  {sharpe} / {sortino} / {calmar}\n"
        f"Max Drawdown:    {max_dd}\n"
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
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    print(f"\n[eod] ========== EOD Session {today} ==========")

    portfolio = get_portfolio()

    if not portfolio["session"]["active"]:
        print("[eod] No active session. Exiting.")
        return

    if portfolio["session"].get("paused"):
        _pause_reason = portfolio["session"].get("pause_reason", "no reason given")
        send_private_only(f"⏸️ <b>EOD skipped — PAUSED</b>\n\nReason: {_pause_reason}\n\nRun <code>python resume_session.py</code> to resume.")
        print(f"[eod] Session is paused ({_pause_reason}). Skipping.")
        return

    session_day = get_session_day()
    total_days = portfolio["session"]["total_days"]
    print(f"[eod] Day {session_day}/{total_days}")

    # Step 0: Resolve expired mid-cap signals (5-day hold)
    resolved_signals = _resolve_midcap_signals(today)
    # Step 0b: Resolve expired penny signals (2-day hold; fallback if midday_check missed any)
    resolved_signals += _resolve_penny_signals(today)

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

    # Step 4b: Scale into confirmed winners (+10% gain, partial not yet taken)
    scaled_positions = _scale_into_winners(portfolio)
    if scaled_positions:
        portfolio = get_portfolio()  # reload after scale-ins

    # Step 5: Update SPY benchmark
    spy_price = None
    try:
        spy_price = get_latest_price("SPY")
        update_spy_benchmark(spy_price)
        print(f"[eod] SPY @ ${spy_price:.2f}")
    except Exception as e:
        print(f"[eod] Could not fetch SPY price: {e}")

    # Step 5b: Record daily equity snapshots
    try:
        if spy_price:
            record_spy_equity(spy_price)
        record_midcap_equity()
        record_penny_equity()
    except Exception as e:
        print(f"[eod] Equity curve snapshot error: {e}")

    # Step 5c: Update QQQ/IWM/GLD benchmark indices
    try:
        update_benchmark_indices({
            "QQQ": get_latest_price("QQQ"),
            "IWM": get_latest_price("IWM"),
            "GLD": get_latest_price("GLD"),
        })
    except Exception as e:
        print(f"[eod] Benchmark indices update failed: {e}")

    # Step 6: Calculate and record equity
    portfolio = get_portfolio()
    equity = _total_equity(portfolio)
    record_equity(equity)

    # Reload one more time for the message (equity_curve + stats updated)
    portfolio = get_portfolio()

    # Step 6a: Reconciliation invariant — catch silent ledger corruption same-day
    try:
        _reconcile_equity(portfolio, equity)
    except Exception as e:
        print(f"[eod] Reconciliation check error (non-critical): {e}")

    # Step 6b: Update agent accuracy tracker with today's closed trades
    try:
        portfolio = update_portfolio_tracker(portfolio)
        # Persist tracker stats back to portfolio.json
        _pf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "portfolio.json")
        with open(_pf_path, "w") as _f:
            json.dump(portfolio, _f, indent=2)
        # Send IC report to private Telegram every 5 closed trades
        total_closed = portfolio.get("agent_tracker", {}).get("total_closed", 0)
        if total_closed > 0 and total_closed % 5 == 0:
            ic_report = format_ic_report(portfolio)
            if ic_report:
                send_private_only(ic_report)
    except Exception as _e:
        print(f"[eod] Agent tracker update failed (non-critical): {_e}")

    # Step 6c: Phase 4 evidence checkpoint (one-time alert once enough post-fix trades close)
    try:
        portfolio = get_portfolio()
        if _check_phase4_evidence(portfolio):
            _pf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "portfolio.json")
            with open(_pf_path, "w") as _f:
                json.dump(portfolio, _f, indent=2)
    except Exception as _e:
        print(f"[eod] Phase 4 checkpoint check failed (non-critical): {_e}")

    # Step 7: Build and send EOD summary
    msg = _build_eod_message(
        portfolio, closed_trades, partial_trades, time_exits,
        trailing_updates, equity, session_day, total_days,
        resolved_signals=resolved_signals,
        scaled_positions=scaled_positions,
    )
    broadcast_message(msg)
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

        spy_line = (
            f"vs SPY:       {'+' if spy_ret >= 0 else ''}{spy_ret:.1f}%\n"
            if spy_ret is not None else "vs SPY:       N/A\n"
        )
        broadcast_message(
            f"🏁 <b>PAPER TRADING SESSION COMPLETE</b>\n\n"
            f"Final Equity: <b>${equity:,.2f}</b>\n"
            f"Total Return: <b>{'+' if final_ret >= 0 else ''}{final_ret:.1f}%</b>\n"
            + spy_line +
            f"Sharpe Ratio: {f'{sharpe:.2f}' if sharpe else 'N/A'}\n"
            f"Win Rate:     {len(winners)}/{len(completed)} trades\n\n"
            f"Check the dashboard for full details."
        )
        print("[eod] Session complete!")
        # Trigger 22-day session summary report
        try:
            import session_summary
            session_summary.run()
        except Exception as e:
            print(f"[eod] Session summary error: {e}")


if __name__ == "__main__":
    main()
