"""
Session manager for the 10-day paper trading session.
Manages all portfolio state in docs/portfolio.json, which is:
  - tracked by git (committed after each GitHub Actions run)
  - served by GitHub Pages (powers the live dashboard)

This module is the single source of truth for session state.
The existing paper_broker.py is NOT used by the session — we manage state directly here.
"""
import json
import math
import os
import statistics
from datetime import datetime, timezone

# Absolute path to docs/portfolio.json, resolved relative to this file's location
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_SYSTEM_DIR = os.path.dirname(_TOOLS_DIR)
PORTFOLIO_FILE = os.path.join(_SYSTEM_DIR, "docs", "portfolio.json")

TOTAL_DAYS = 10
INITIAL_CAPITAL = 5_000.0

# Circuit breaker thresholds
MAX_SESSION_DRAWDOWN = 0.15   # halt if equity drops 15%+ from peak
MAX_DAILY_LOSS = 0.03         # skip day if equity dropped 3%+ since yesterday


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load() -> dict:
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE) as f:
            return json.load(f)
    return _default_portfolio()


def _default_portfolio() -> dict:
    return {
        "session": {
            "start_date": None,
            "current_day": 0,
            "total_days": TOTAL_DAYS,
            "active": False,
            "last_updated": None,
        },
        "cash": INITIAL_CAPITAL,
        "initial_capital": INITIAL_CAPITAL,
        "equity": INITIAL_CAPITAL,
        "peak_equity": INITIAL_CAPITAL,
        "circuit_breaker": {
            "triggered": False,
            "reason": None,
            "triggered_date": None,
        },
        "stats": {
            "sharpe": None,
            "sortino": None,
            "calmar": None,
            "max_drawdown_pct": None,
            "spy_start_price": None,
            "spy_current_price": None,
            "benchmark_return_pct": None,
        },
        "positions": {},
        "trade_history": [],
        "equity_curve": [],
        "journal": [],
    }


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(PORTFOLIO_FILE), exist_ok=True)
    data["session"]["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _migrate(p: dict) -> dict:
    """Add any missing keys to an existing portfolio (backward compat)."""
    p.setdefault("peak_equity", p.get("equity", INITIAL_CAPITAL))
    p.setdefault("circuit_breaker", {"triggered": False, "reason": None, "triggered_date": None})
    p.setdefault("stats", {
        "sharpe": None,
        "sortino": None,
        "calmar": None,
        "max_drawdown_pct": None,
        "spy_start_price": None,
        "spy_current_price": None,
        "benchmark_return_pct": None,
    })
    p.setdefault("journal", [])
    return p


# ---------------------------------------------------------------------------
# Public API — portfolio access
# ---------------------------------------------------------------------------

def get_portfolio() -> dict:
    """Return the full portfolio state dict."""
    return _migrate(_load())


def save_portfolio(data: dict) -> None:
    """Persist portfolio state (use sparingly — prefer the helpers below)."""
    _save(data)


def is_session_active() -> bool:
    """True if the session is running and has days remaining."""
    p = _load()
    s = p.get("session", {})
    return s.get("active", False) and s.get("current_day", 0) <= s.get("total_days", TOTAL_DAYS)


def start_session() -> dict:
    """
    Initialise a fresh 10-day session.
    Resets cash and positions to starting values.
    """
    p = _default_portfolio()
    p["session"]["active"] = True
    p["session"]["start_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    p["session"]["current_day"] = 1
    _save(p)
    print(f"[session] New session started. Day 1/{TOTAL_DAYS}. Capital: ${INITIAL_CAPITAL:,.2f}")
    return p


def get_session_day() -> int:
    return _load()["session"].get("current_day", 0)


def advance_day() -> int:
    """Increment the session day counter. Marks session inactive after day 10."""
    p = _load()
    new_day = p["session"].get("current_day", 0) + 1
    p["session"]["current_day"] = new_day
    if new_day > p["session"].get("total_days", TOTAL_DAYS):
        p["session"]["active"] = False
        print("[session] Session complete — all 10 days finished.")
    _save(p)
    return new_day


# ---------------------------------------------------------------------------
# Equity & peak tracking
# ---------------------------------------------------------------------------

def record_equity(equity: float) -> None:
    """Append today's closing equity to the equity curve and update all metrics."""
    p = _migrate(_load())
    p["equity"] = round(equity, 2)

    # Update peak equity (high-water mark)
    if equity > p.get("peak_equity", equity):
        p["peak_equity"] = round(equity, 2)

    p["equity_curve"].append({
        "day": p["session"].get("current_day", 0),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "equity": round(equity, 2),
    })

    curve = p["equity_curve"]
    if len(curve) >= 2:
        equities   = [e["equity"] for e in curve]
        daily_rets = [(equities[i] - equities[i-1]) / equities[i-1] for i in range(1, len(equities))]

        if len(daily_rets) >= 2:
            try:
                mean_r = statistics.mean(daily_rets)
                std_r  = statistics.stdev(daily_rets)
                ann    = 252 ** 0.5

                # Sharpe (all volatility penalised)
                if std_r > 0:
                    p["stats"]["sharpe"] = round((mean_r / std_r) * ann, 2)

                # Sortino (only downside volatility penalised)
                neg_rets = [r for r in daily_rets if r < 0]
                if len(neg_rets) >= 2:
                    down_std = statistics.stdev(neg_rets)
                    if down_std > 0:
                        p["stats"]["sortino"] = round((mean_r / down_std) * ann, 2)

            except Exception:
                pass

        # Max drawdown (peak-to-trough over session)
        peak_so_far = equities[0]
        max_dd = 0.0
        for eq in equities:
            if eq > peak_so_far:
                peak_so_far = eq
            dd = (peak_so_far - eq) / peak_so_far
            if dd > max_dd:
                max_dd = dd
        p["stats"]["max_drawdown_pct"] = round(max_dd * 100, 2)

        # Calmar = annualised return / max drawdown
        days_elapsed = len(curve)
        initial = p.get("initial_capital", INITIAL_CAPITAL)
        if days_elapsed >= 2 and initial > 0 and max_dd > 0:
            try:
                total_ret = (equity / initial) - 1
                ann_ret   = (1 + total_ret) ** (252 / days_elapsed) - 1
                p["stats"]["calmar"] = round(ann_ret / max_dd, 2)
            except Exception:
                pass

    _save(p)


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

def check_circuit_breaker(current_equity: float) -> tuple:
    """
    Check if trading should halt due to drawdown limits.
    Returns (should_halt: bool, reason: str | None).

    Rules:
      - Session drawdown > 15% from peak → halt
      - Daily loss > 3% vs yesterday's equity → skip day
    """
    p = _migrate(_load())

    # Already triggered — stay halted
    if p["circuit_breaker"].get("triggered"):
        return True, p["circuit_breaker"].get("reason", "Circuit breaker previously triggered")

    peak = p.get("peak_equity", INITIAL_CAPITAL)
    initial = p.get("initial_capital", INITIAL_CAPITAL)

    # 15% drawdown from peak
    if peak > 0:
        drawdown = (peak - current_equity) / peak
        if drawdown >= MAX_SESSION_DRAWDOWN:
            reason = f"Session drawdown {drawdown*100:.1f}% from peak ${peak:,.2f} — halting trading"
            set_circuit_breaker(reason)
            return True, reason

    # 3% daily loss vs yesterday's equity
    curve = p.get("equity_curve", [])
    if curve:
        yesterday_equity = curve[-1]["equity"]
        daily_loss = (yesterday_equity - current_equity) / yesterday_equity
        if daily_loss >= MAX_DAILY_LOSS:
            reason = f"Daily loss {daily_loss*100:.1f}% vs yesterday ${yesterday_equity:,.2f} — skipping today"
            return True, reason

    return False, None


def set_circuit_breaker(reason: str) -> None:
    """Permanently trigger the circuit breaker (halts all future trading)."""
    p = _migrate(_load())
    p["circuit_breaker"] = {
        "triggered": True,
        "reason": reason,
        "triggered_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    _save(p)
    print(f"[session] CIRCUIT BREAKER TRIGGERED: {reason}")


# ---------------------------------------------------------------------------
# Position management
# ---------------------------------------------------------------------------

def open_position(
    ticker: str,
    qty: int,
    entry_price: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    journal_note: str = "",
) -> None:
    """
    Record a newly opened long position and deduct cash.
    stop_loss_pct / take_profit_pct are fractions (e.g. 0.03 = 3%).
    """
    p = _migrate(_load())
    cost = round(entry_price * qty, 2)

    if cost > p["cash"]:
        # Scale down qty to available cash
        qty = int(p["cash"] / entry_price)
        cost = round(entry_price * qty, 2)

    if qty <= 0:
        print(f"[session] Cannot open position in {ticker}: insufficient cash.")
        return

    sl_price = round(entry_price * (1 - stop_loss_pct), 2)
    tp_price = round(entry_price * (1 + take_profit_pct), 2)
    # 1:1 R/R partial profit level (halfway between entry and TP)
    partial_price = round(entry_price * (1 + stop_loss_pct), 2)

    p["cash"] = round(p["cash"] - cost, 2)
    p["positions"][ticker] = {
        "qty": qty,
        "entry_price": round(entry_price, 2),
        "cost_basis": cost,
        "stop_loss": sl_price,
        "take_profit": tp_price,
        "partial_profit_price": partial_price,   # price at which we take 50% off
        "stop_loss_pct": round(stop_loss_pct * 100, 2),
        "take_profit_pct": round(take_profit_pct * 100, 2),
        "highest_price": round(entry_price, 2),  # for trailing stop
        "partial_taken": False,                   # partial profit already executed?
        "opened_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "journal_note": journal_note,
    }
    _save(p)
    print(f"[session] Opened {ticker}: {qty} shares @ ${entry_price:.2f}  TP=${tp_price:.2f}  SL=${sl_price:.2f}  Partial@${partial_price:.2f}")


def close_position(ticker: str, exit_price: float, reason: str) -> dict:
    """
    Close an open position at exit_price and add proceeds to cash.
    reason: 'take_profit' | 'stop_loss' | 'manual' | 'time_exit' | 'partial_final'
    Returns a trade record dict (also appended to trade_history).
    """
    p = _migrate(_load())
    pos = p["positions"].get(ticker)
    if not pos:
        print(f"[session] close_position: no open position for {ticker}")
        return {}

    proceeds = round(exit_price * pos["qty"], 2)
    pnl = round(proceeds - pos["cost_basis"], 2)
    pnl_pct = round(pnl / pos["cost_basis"] * 100, 2)

    trade = {
        "ticker": ticker,
        "qty": pos["qty"],
        "entry_price": pos["entry_price"],
        "exit_price": round(exit_price, 2),
        "cost_basis": pos["cost_basis"],
        "proceeds": proceeds,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "reason": reason,
        "opened_date": pos.get("opened_date"),
        "closed_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "journal_note": pos.get("journal_note", ""),
    }

    p["cash"] = round(p["cash"] + proceeds, 2)
    del p["positions"][ticker]
    p["trade_history"].append(trade)
    _save(p)
    print(f"[session] Closed {ticker} @ ${exit_price:.2f}  P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)  Reason: {reason}")
    return trade


def partial_close_position(ticker: str, qty_to_close: int, exit_price: float) -> dict:
    """
    Close part of a position (partial profit taking).
    Moves stop loss to breakeven after partial close.
    Returns a partial trade record.
    """
    p = _migrate(_load())
    pos = p["positions"].get(ticker)
    if not pos:
        print(f"[session] partial_close: no open position for {ticker}")
        return {}

    qty_to_close = min(qty_to_close, pos["qty"])
    if qty_to_close <= 0:
        return {}

    partial_cost = round(pos["entry_price"] * qty_to_close, 2)
    proceeds = round(exit_price * qty_to_close, 2)
    pnl = round(proceeds - partial_cost, 2)
    pnl_pct = round(pnl / partial_cost * 100, 2)

    # Update remaining position
    remaining_qty = pos["qty"] - qty_to_close
    remaining_cost = round(pos["entry_price"] * remaining_qty, 2)

    p["cash"] = round(p["cash"] + proceeds, 2)

    if remaining_qty > 0:
        p["positions"][ticker]["qty"] = remaining_qty
        p["positions"][ticker]["cost_basis"] = remaining_cost
        p["positions"][ticker]["partial_taken"] = True
        # Move stop loss to breakeven
        p["positions"][ticker]["stop_loss"] = pos["entry_price"]
        print(f"[session] Partial close {ticker}: sold {qty_to_close} @ ${exit_price:.2f}, SL moved to breakeven ${pos['entry_price']:.2f}")
    else:
        del p["positions"][ticker]

    # Log partial trade to history
    partial_trade = {
        "ticker": ticker,
        "qty": qty_to_close,
        "entry_price": pos["entry_price"],
        "exit_price": round(exit_price, 2),
        "cost_basis": partial_cost,
        "proceeds": proceeds,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "reason": "partial_profit",
        "opened_date": pos.get("opened_date"),
        "closed_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "journal_note": pos.get("journal_note", ""),
    }
    p["trade_history"].append(partial_trade)
    _save(p)
    print(f"[session] Partial P&L {ticker}: +${pnl:.2f} ({pnl_pct:.1f}%)")
    return partial_trade


def update_trailing_stop(ticker: str, current_price: float) -> bool:
    """
    Ratchet the stop loss upward if current price exceeds the previous high.
    Trail = original stop_loss_pct below highest_price.
    Returns True if stop was updated.
    """
    p = _migrate(_load())
    pos = p["positions"].get(ticker)
    if not pos:
        return False

    prev_high = pos.get("highest_price", pos["entry_price"])
    if current_price <= prev_high:
        return False

    # Update high-water mark
    p["positions"][ticker]["highest_price"] = round(current_price, 2)

    # New SL = highest_price × (1 - original_sl_pct)
    sl_pct = pos["stop_loss_pct"] / 100
    new_sl = round(current_price * (1 - sl_pct), 2)
    old_sl = pos["stop_loss"]

    if new_sl > old_sl:
        p["positions"][ticker]["stop_loss"] = new_sl
        _save(p)
        print(f"[session] Trailing stop updated {ticker}: SL ${old_sl:.2f} → ${new_sl:.2f} (high ${current_price:.2f})")
        return True

    _save(p)
    return False


# ---------------------------------------------------------------------------
# Stats & journal
# ---------------------------------------------------------------------------

def update_spy_benchmark(spy_price: float) -> None:
    """Update current SPY price and recalculate benchmark return."""
    p = _migrate(_load())
    start = p["stats"].get("spy_start_price")
    p["stats"]["spy_current_price"] = round(spy_price, 2)
    if start and start > 0:
        p["stats"]["benchmark_return_pct"] = round((spy_price - start) / start * 100, 2)
    _save(p)


def set_spy_start_price(spy_price: float) -> None:
    """Record SPY price at session start for benchmark comparison."""
    p = _migrate(_load())
    if not p["stats"].get("spy_start_price"):
        p["stats"]["spy_start_price"] = round(spy_price, 2)
        _save(p)
        print(f"[session] SPY benchmark start price: ${spy_price:.2f}")


def add_journal_entry(entry: dict) -> None:
    """
    Append an entry to the trade journal.
    entry should include: date, ticker, action, rationale, bull_case, bear_case
    """
    p = _migrate(_load())
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    p["journal"].append(entry)
    _save(p)
