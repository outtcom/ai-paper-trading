"""
Morning session entry point.
Triggered by GitHub Actions at 7:30 AM ET on weekdays.

Flow:
  1. Start/check the 10-day session
  2. Circuit breaker — halt if session drawdown > 15% or daily loss > 3%
  3. FOMC / CPI / NFP auto-block — no trades on macro event days
  4. Check VIX — if EXTREME, skip trading. Apply sizing multiplier otherwise.
  5. Run the 7-agent pipeline (dry_run=True) on all watchlist tickers (stocks + crypto)
  6. Skip any ticker with earnings within 3 days (gap risk)
  7. Pick the single highest-conviction BUY signal
  8. Send a Telegram approval card (TP, SL, thesis, VIX context)
  9. Poll for 60 min — if approved, execute the paper trade
  10. Log trade journal entry

Usage:
  python morning_session.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import WATCHLIST, APPROVAL_TIMEOUT_SECONDS
from orchestrator import run_pipeline
from tools.market_data import get_latest_price
from tools.market_regime import get_vix_multiplier, has_earnings_soon, is_event_blocked
from tools.session_manager import (
    add_journal_entry,
    check_circuit_breaker,
    get_portfolio,
    get_session_day,
    is_session_active,
    open_position,
    record_equity,
    set_spy_start_price,
    start_session,
)
from tools.telegram_bot import poll_for_response, send_approval_request, send_message

# Conviction string → numeric rank for comparison
_CONVICTION_RANK = {"high": 3, "medium": 2, "low": 1}


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def _analyze_all(date: str) -> dict:
    """Run dry-run pipeline for every watchlist ticker. Returns {ticker: state}."""
    results = {}
    for ticker in WATCHLIST:
        try:
            print(f"[morning] Analyzing {ticker}...")
            state = run_pipeline(ticker, date, dry_run=True)
            results[ticker] = state
        except Exception as e:
            print(f"[morning] Pipeline error for {ticker}: {e}")
            results[ticker] = {
                "error": str(e),
                "final_order": {"action": "hold", "qty": 0},
            }
    return results


def _pick_best(results: dict, blocked: set):
    """
    Filter for BUY signals, skip earnings-blocked tickers, pick highest conviction.
    Returns (ticker, state) or (None, None) if no buys.
    """
    candidates = []
    for ticker, state in results.items():
        if ticker in blocked:
            print(f"[morning] {ticker} blocked — earnings within 3 days.")
            continue
        order = state.get("final_order", {})
        if order.get("action") == "buy" and order.get("qty", 0) > 0:
            conviction_str = state.get("trader_decision", {}).get("conviction", "low")
            score = _CONVICTION_RANK.get(str(conviction_str).lower(), 0)
            candidates.append((score, ticker, state))

    if not candidates:
        return None, None

    candidates.sort(key=lambda x: x[0], reverse=True)
    _, ticker, state = candidates[0]
    return ticker, state


def _build_summary(ticker: str, state: dict, session_day: int, total_days: int, cash: float, **kwargs) -> dict:
    """Construct the trade_summary dict required by send_approval_request."""
    order = state.get("final_order", {})
    trader = state.get("trader_decision", {})

    current_price = get_latest_price(ticker)

    # SL from fund manager, fall back to trader, then default 3%
    sl_pct_raw = (
        order.get("stop_loss_pct")
        or trader.get("stop_loss_pct")
        or 0.03
    )
    tp_pct_raw = sl_pct_raw * 2  # 2:1 reward-to-risk

    # Position sizing: fund manager fraction × VIX multiplier, cap at cash
    position_fraction = order.get("position_size_pct") or 0.25
    vix_mult = kwargs.get("vix_multiplier", 1.0)
    max_usd = cash * position_fraction * vix_mult
    qty = max(1, int(max_usd / current_price))
    actual_usd = round(qty * current_price, 2)

    # Narrative fields — trimmed to keep Telegram message readable
    why = (order.get("final_reasoning") or trader.get("reasoning") or "No reasoning provided.")[:350]
    bull = (state.get("bull_case") or "")[:140]
    bear = (state.get("bear_case") or "")[:140]

    return {
        "ticker": ticker,
        "current_price": current_price,
        "conviction": trader.get("conviction", "medium"),
        "why": why,
        "bull_case": bull or "See logs for full bull case.",
        "bear_case": bear or "See logs for full bear case.",
        "take_profit": round(current_price * (1 + tp_pct_raw), 2),
        "take_profit_pct": tp_pct_raw * 100,
        "stop_loss": round(current_price * (1 - sl_pct_raw), 2),
        "stop_loss_pct": sl_pct_raw * 100,
        "position_size_usd": actual_usd,
        "qty": qty,
        "session_day": session_day,
        "total_days": total_days,
        "vix_label": kwargs.get("vix_label", ""),
        # Raw fractions stored separately for session_manager.open_position()
        "_sl_pct_raw": sl_pct_raw,
        "_tp_pct_raw": tp_pct_raw,
        # Full reasoning for journal
        "_full_why": order.get("final_reasoning") or trader.get("reasoning") or "",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    today = datetime.today().strftime("%Y-%m-%d")
    print(f"\n[morning] ========== Morning Session {today} ==========")

    portfolio = get_portfolio()

    # First run: initialise the session
    if not portfolio["session"]["active"]:
        print("[morning] No active session — starting a new 10-day session.")
        portfolio = start_session()

    # Session finished
    if not is_session_active():
        send_message(
            "🏁 <b>Paper trading session complete!</b>\n\n"
            "All 10 days are done. Check the dashboard for your final results."
        )
        print("[morning] Session complete. Nothing to do.")
        return

    session_day = get_session_day()
    total_days = portfolio["session"]["total_days"]
    equity = portfolio.get("equity", portfolio["initial_capital"])
    cash = portfolio.get("cash", portfolio["initial_capital"])

    print(f"[morning] Day {session_day}/{total_days}  |  Equity: ${equity:,.2f}  |  Cash: ${cash:,.2f}")

    # --- Record SPY start price for benchmark (only on day 1) ---
    if session_day == 1 and not portfolio.get("stats", {}).get("spy_start_price"):
        try:
            spy_price = get_latest_price("SPY")
            set_spy_start_price(spy_price)
            print(f"[morning] SPY benchmark anchored at ${spy_price:.2f}")
        except Exception as e:
            print(f"[morning] Could not fetch SPY start price: {e}")

    # --- Circuit breaker — check drawdown limits ---
    halt, halt_reason = check_circuit_breaker(equity)
    if halt:
        print(f"[morning] CIRCUIT BREAKER: {halt_reason}")
        send_message(
            f"🚨 <b>CIRCUIT BREAKER — Day {session_day}/{total_days}</b>\n\n"
            f"{halt_reason}\n\n"
            f"<b>Trading halted to protect capital.</b>\n"
            f"Session equity: ${equity:,.2f}\n"
            f"<i>No trades will be placed until circuit breaker is reviewed.</i>"
        )
        record_equity(equity)
        return

    # --- FOMC / CPI / NFP auto-block ---
    event_blocked, event_reason = is_event_blocked(today)
    if event_blocked:
        print(f"[morning] Macro event block: {event_reason}")
        send_message(
            f"📅 <b>No Trade — Macro Event Day — Day {session_day}/{total_days}</b>\n\n"
            f"🚫 {event_reason}\n\n"
            f"Markets often make violent moves on these days. Staying in cash.\n"
            f"Session equity: <b>${equity:,.2f}</b>"
        )
        record_equity(equity)
        return

    # --- VIX check — gate on market volatility regime ---
    vix_multiplier, vix_label = get_vix_multiplier()
    print(f"[morning] VIX regime: {vix_label}  |  Sizing multiplier: {vix_multiplier}x")

    if vix_multiplier == 0.0:
        send_message(
            f"🚨 <b>No Trade — VIX Extreme — Day {session_day}/{total_days}</b>\n\n"
            f"VIX: {vix_label}\n"
            f"Markets are too volatile. Staying in cash today.\n\n"
            f"Session equity: <b>${equity:,.2f}</b>"
        )
        record_equity(equity)
        print("[morning] VIX EXTREME — skipping all trades.")
        return

    # --- Earnings check — identify tickers to block ---
    earnings_blocked = set()
    for t in WATCHLIST:
        e = has_earnings_soon(t, days=3)
        if e["has_earnings"]:
            earnings_blocked.add(t)
            print(f"[morning] {t} blocked — earnings on {e['date']} ({e['days_until']}d)")

    send_message(
        f"🔍 <b>Day {session_day}/{total_days}</b> — Analysing {len(WATCHLIST)} tickers "
        f"(stocks + crypto)...\n"
        f"VIX: {vix_label}\n"
        f"<i>Back in ~10–15 min with the best trade.</i>"
    )

    # Run the agent pipeline for all tickers
    results = _analyze_all(today)
    ticker, state = _pick_best(results, blocked=earnings_blocked)

    if ticker is None:
        blocked_note = f"\nEarnings-blocked: {', '.join(earnings_blocked)}" if earnings_blocked else ""
        no_trade_msg = (
            f"📭 <b>No Trade Today — Day {session_day}/{total_days}</b>\n\n"
            f"No valid BUY signals across {len(WATCHLIST)} tickers.{blocked_note}\n\n"
            f"Session equity: <b>${equity:,.2f}</b>"
        )
        send_message(no_trade_msg)
        record_equity(equity)
        print("[morning] No BUY signals found. Day logged.")
        return

    # Build and send the approval card
    summary = _build_summary(
        ticker, state, session_day, total_days, cash,
        vix_multiplier=vix_multiplier, vix_label=vix_label,
    )
    print(f"[morning] Best opportunity: {ticker} (conviction: {summary['conviction']})")
    send_approval_request(summary)
    print(f"[morning] Approval request sent. Polling for up to {APPROVAL_TIMEOUT_SECONDS // 60} min...")

    response = poll_for_response(timeout_seconds=APPROVAL_TIMEOUT_SECONDS)
    print(f"[morning] Response received: {response}")

    if response == "approved":
        open_position(
            ticker=ticker,
            qty=summary["qty"],
            entry_price=summary["current_price"],
            stop_loss_pct=summary["_sl_pct_raw"],
            take_profit_pct=summary["_tp_pct_raw"],
            journal_note=summary["_full_why"][:500],
        )
        # Add journal entry with full trade rationale
        add_journal_entry({
            "date": today,
            "day": session_day,
            "ticker": ticker,
            "action": "BUY",
            "entry_price": summary["current_price"],
            "qty": summary["qty"],
            "conviction": summary["conviction"],
            "stop_loss": summary["stop_loss"],
            "take_profit": summary["take_profit"],
            "vix_label": vix_label,
            "rationale": summary["_full_why"],
            "bull_case": summary["bull_case"],
            "bear_case": summary["bear_case"],
        })
        send_message(
            f"✅ <b>Trade Executed — {ticker}</b>\n\n"
            f"BUY {summary['qty']} shares @ ${summary['current_price']:.2f}\n"
            f"Total deployed: ${summary['position_size_usd']:.2f}\n\n"
            f"TP: ${summary['take_profit']:.2f}  |  SL: ${summary['stop_loss']:.2f}\n"
            f"Partial profit at: ${round(summary['current_price'] * (1 + summary['_sl_pct_raw']), 2):.2f} "
            f"(1:1 R/R — 50% sold, rest runs to TP)\n\n"
            f"<i>I'll check TP/SL levels at market close and send an EOD summary.</i>"
        )
        record_equity(equity)

    elif response == "rejected":
        send_message(
            f"⏭ <b>Trade Skipped — Day {session_day}/{total_days}</b>\n\n"
            f"No position opened today. See you tomorrow!"
        )
        record_equity(equity)

    else:  # timeout
        send_message(
            f"⏰ <b>60-min window expired — Day {session_day}/{total_days}</b>\n\n"
            f"No response received. Trade skipped for today."
        )
        record_equity(equity)

    print(f"[morning] Day {session_day} complete.")


if __name__ == "__main__":
    main()
