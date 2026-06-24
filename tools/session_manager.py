"""
Session manager for the 90-day paper trading session.
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
from datetime import datetime, timezone, timedelta

# Absolute path to docs/portfolio.json, resolved relative to this file's location
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_SYSTEM_DIR = os.path.dirname(_TOOLS_DIR)
PORTFOLIO_FILE = os.path.join(_SYSTEM_DIR, "docs", "portfolio.json")

from config import SESSION_DAYS as TOTAL_DAYS   # single source of truth — edit config.py
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
        "open_orders": [],
        "trade_history": [],
        "equity_curve": [],
        "journal": [],
        "day_trade_signals_archive": [],  # historical day trade signals (pre-Session 3)
        "midcap_signals":  [],
        "midcap_capital":  {"initial": 5000.0, "cash": 5000.0, "equity": 5000.0},
        "penny_signals":   [],
        "penny_capital":   {"initial": 5000.0, "cash": 5000.0, "equity": 5000.0},
        "spy_equity_curve":    [],   # [{day, date, equity}] SPY normalized to $5000 start
        "midcap_equity_curve": [],   # [{day, date, equity}] mid-cap pool daily snapshots
        "penny_equity_curve":  [],   # [{day, date, equity}] penny pool daily snapshots
        "benchmark_indices": {
            "SPY": {"start_price": None, "current_price": None, "return_pct": None},
            "QQQ": {"start_price": None, "current_price": None, "return_pct": None},
            "IWM": {"start_price": None, "current_price": None, "return_pct": None},
            "GLD": {"start_price": None, "current_price": None, "return_pct": None},
        },
        "sector_strength":   {},   # latest sector heatmap from morning session
        "strategy_brief":    {},   # latest strategy consultant output
        "index_etf_signals": {},   # latest pipeline signals for SPY/QQQ/IWM/GLD
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
    p.setdefault("open_orders", [])
    # Archive old day trade signals if they exist under the legacy key
    if "day_trade_signals" in p and "day_trade_signals_archive" not in p:
        p["day_trade_signals_archive"] = p.pop("day_trade_signals")
    p.setdefault("day_trade_signals_archive", [])
    # Remove stale capital pools from previous strategy
    p.pop("day_trade_capital", None)
    p.pop("scalping_capital",  None)
    p.setdefault("midcap_signals",  [])
    p.setdefault("midcap_capital",  {"initial": 5000.0, "cash": 5000.0, "equity": 5000.0})
    p.setdefault("penny_signals",   [])
    p.setdefault("penny_capital",   {"initial": 5000.0, "cash": 5000.0, "equity": 5000.0})
    p.setdefault("spy_equity_curve", [])
    p.setdefault("midcap_equity_curve", [])
    p.setdefault("penny_equity_curve",  [])
    # Remove stale equity curves
    p.pop("dt_equity_curve",       None)
    p.pop("scalping_equity_curve", None)
    bi_default = p.setdefault("benchmark_indices", {})
    for _etf in ("SPY", "QQQ", "IWM", "GLD"):
        bi_default.setdefault(_etf, {"start_price": None, "current_price": None, "return_pct": None})
    p.setdefault("sector_strength", {})
    p.setdefault("strategy_brief", {})
    p.setdefault("index_etf_signals", {})
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
    Initialise a fresh session (TOTAL_DAYS trading days).
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


def already_ran_today(script: str) -> bool:
    """
    Idempotency guard — returns True if this script already completed today.
    Prevents double-execution when both EDT and EST crons fire within the same window.
    """
    from datetime import timezone
    today = datetime.now(timezone(timedelta(hours=-4))).strftime("%Y-%m-%d")  # ET date
    p = _load()
    return p.get("last_run_dates", {}).get(script) == today


def mark_ran_today(script: str) -> None:
    """Record that this script completed today so duplicate runs can be skipped."""
    from datetime import timezone
    today = datetime.now(timezone(timedelta(hours=-4))).strftime("%Y-%m-%d")
    p = _migrate(_load())
    if "last_run_dates" not in p:
        p["last_run_dates"] = {}
    p["last_run_dates"][script] = today
    _save(p)


def advance_day() -> int:
    """Increment the session day counter. Marks session inactive after day TOTAL_DAYS."""
    p = _load()
    new_day = p["session"].get("current_day", 0) + 1
    p["session"]["current_day"] = new_day
    if new_day > p["session"].get("total_days", TOTAL_DAYS):
        p["session"]["active"] = False
        print(f"[session] Session complete — all {p['session']['total_days']} days finished.")
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
    direction: str = "long",
) -> None:
    """
    Record a newly opened position and update cash.
    stop_loss_pct / take_profit_pct are fractions (e.g. 0.03 = 3%).
    direction: 'long' (default) or 'short'
    For shorts: cash increases (proceeds received); reversed TP/SL levels.
    """
    p = _migrate(_load())
    notional = round(entry_price * qty, 2)

    if direction == "long":
        if notional > p["cash"]:
            qty = int(p["cash"] / entry_price)
            notional = round(entry_price * qty, 2)
        if qty <= 0:
            print(f"[session] Cannot open long {ticker}: insufficient cash.")
            return
        sl_price      = round(entry_price * (1 - stop_loss_pct), 2)
        tp_price      = round(entry_price * (1 + take_profit_pct), 2)
        partial_price = round(entry_price * (1 + stop_loss_pct), 2)  # 1:1 R/R upside
        p["cash"] = round(p["cash"] - notional, 2)
    else:
        # Short: receive proceeds, owe position at current price
        sl_price      = round(entry_price * (1 + stop_loss_pct), 2)   # SL above entry
        tp_price      = round(entry_price * (1 - take_profit_pct), 2) # TP below entry
        partial_price = round(entry_price * (1 - stop_loss_pct), 2)   # 1:1 R/R downside
        p["cash"] = round(p["cash"] + notional, 2)  # receive short proceeds

    p["positions"][ticker] = {
        "direction": direction,
        "qty": qty,
        "entry_price": round(entry_price, 2),
        "cost_basis": notional,
        "last_price": round(entry_price, 2),     # updated by midday/EOD for live P&L
        "stop_loss": sl_price,
        "take_profit": tp_price,
        "partial_profit_price": partial_price,
        "stop_loss_pct": round(stop_loss_pct * 100, 2),
        "take_profit_pct": round(take_profit_pct * 100, 2),
        "highest_price": round(entry_price, 2),  # for trailing stop (longs)
        "lowest_price": round(entry_price, 2),   # for trailing stop (shorts)
        "partial_taken": False,
        "opened_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "journal_note": journal_note,
    }
    _save(p)
    dir_tag = "SHORT" if direction == "short" else "LONG"
    print(f"[session] Opened {dir_tag} {ticker}: {qty} shares @ ${entry_price:.2f}  TP=${tp_price:.2f}  SL=${sl_price:.2f}  Partial@${partial_price:.2f}")


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

    direction = pos.get("direction", "long")
    cover_cost = round(exit_price * pos["qty"], 2)

    if direction == "short":
        # Short: pay to cover. Cash already received proceeds at open.
        pnl = round((pos["entry_price"] - exit_price) * pos["qty"], 2)
        proceeds = round(pos["cost_basis"] + pnl, 2)  # notional returned +/- P&L
        p["cash"] = round(p["cash"] - cover_cost, 2)
    else:
        proceeds = cover_cost
        pnl = round(proceeds - pos["cost_basis"], 2)
        p["cash"] = round(p["cash"] + proceeds, 2)

    pnl_pct = round(pnl / pos["cost_basis"] * 100, 2)

    trade = {
        "ticker": ticker,
        "direction": direction,
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
    del p["positions"][ticker]
    p["trade_history"].append(trade)
    _save(p)
    dir_tag = "SHORT" if direction == "short" else "LONG"
    print(f"[session] Closed {dir_tag} {ticker} @ ${exit_price:.2f}  P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)  Reason: {reason}")
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

    direction = pos.get("direction", "long")
    partial_cost = round(pos["entry_price"] * qty_to_close, 2)
    cover_cost   = round(exit_price * qty_to_close, 2)

    if direction == "short":
        pnl = round((pos["entry_price"] - exit_price) * qty_to_close, 2)
        p["cash"] = round(p["cash"] - cover_cost, 2)
    else:
        pnl = round(cover_cost - partial_cost, 2)
        p["cash"] = round(p["cash"] + cover_cost, 2)

    pnl_pct = round(pnl / partial_cost * 100, 2)

    # Update remaining position
    remaining_qty  = pos["qty"] - qty_to_close
    remaining_cost = round(pos["entry_price"] * remaining_qty, 2)

    if remaining_qty > 0:
        p["positions"][ticker]["qty"]         = remaining_qty
        p["positions"][ticker]["cost_basis"]  = remaining_cost
        p["positions"][ticker]["partial_taken"] = True
        # Move stop loss to breakeven
        p["positions"][ticker]["stop_loss"] = pos["entry_price"]
        print(f"[session] Partial close {ticker}: {'covered' if direction == 'short' else 'sold'} {qty_to_close} @ ${exit_price:.2f}, SL moved to breakeven ${pos['entry_price']:.2f}")
    else:
        del p["positions"][ticker]

    # Log partial trade to history
    partial_trade = {
        "ticker": ticker,
        "direction": direction,
        "qty": qty_to_close,
        "entry_price": pos["entry_price"],
        "exit_price": round(exit_price, 2),
        "cost_basis": partial_cost,
        "proceeds": round(partial_cost + pnl, 2),
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
    Ratchet the stop loss as price moves favorably — with three tiers:

    Tier 1 — Standard trail (sl_pct below new high):
      Activates immediately on any new high.

    Tier 2 — Breakeven promotion (+8% gain):
      Once the position is up 8%, the stop is floored at entry price.
      Can never turn a winner into a loser after this point.

    Tier 3 — Tight trail (+15% gain):
      Switches to sl_pct / 2 so gains are locked in more aggressively.
      Lets big movers (like MU) run while protecting accumulated profit.

    Returns True if stop was updated.
    """
    BREAKEVEN_TRIGGER = 0.08   # promote stop to entry when up 8%
    TIGHT_TRAIL_TRIGGER = 0.15  # halve the trail percentage when up 15%

    p = _migrate(_load())
    pos = p["positions"].get(ticker)
    if not pos:
        return False

    p["positions"][ticker]["last_price"] = round(current_price, 2)
    sl_pct    = pos["stop_loss_pct"] / 100
    direction = pos.get("direction", "long")
    entry     = pos["entry_price"]
    old_sl    = pos["stop_loss"]

    if direction == "long":
        pnl_pct   = (current_price - entry) / entry
        prev_high = pos.get("highest_price", entry)

        # Track new high
        if current_price > prev_high:
            p["positions"][ticker]["highest_price"] = round(current_price, 2)
            prev_high = current_price

        # Tier 3: tighter trail once deeply in profit
        trail_pct = sl_pct / 2 if pnl_pct >= TIGHT_TRAIL_TRIGGER else sl_pct

        # Trail at trail_pct below the highest price seen
        new_sl = round(prev_high * (1 - trail_pct), 2)

        # Tier 2: breakeven floor — stop can never go below entry once up 8%
        if pnl_pct >= BREAKEVEN_TRIGGER:
            new_sl = max(new_sl, entry)

        if new_sl > old_sl:
            p["positions"][ticker]["stop_loss"] = new_sl
            _save(p)
            if new_sl >= entry > old_sl:
                print(f"[session] BREAKEVEN promoted {ticker}: SL ${old_sl:.2f} → ${new_sl:.2f} (entry ${entry:.2f}, +{pnl_pct:.1%})")
            elif pnl_pct >= TIGHT_TRAIL_TRIGGER:
                print(f"[session] TIGHT trail {ticker}: SL ${old_sl:.2f} → ${new_sl:.2f} (high ${prev_high:.2f}, +{pnl_pct:.1%})")
            else:
                print(f"[session] Trail {ticker}: SL ${old_sl:.2f} → ${new_sl:.2f} (high ${prev_high:.2f}, +{pnl_pct:.1%})")
            return True

    else:  # short
        pnl_pct  = (entry - current_price) / entry
        prev_low = pos.get("lowest_price", entry)

        if current_price < prev_low:
            p["positions"][ticker]["lowest_price"] = round(current_price, 2)
            prev_low = current_price

        trail_pct = sl_pct / 2 if pnl_pct >= TIGHT_TRAIL_TRIGGER else sl_pct
        new_sl    = round(prev_low * (1 + trail_pct), 2)

        if pnl_pct >= BREAKEVEN_TRIGGER:
            new_sl = min(new_sl, entry)

        if new_sl < old_sl:
            p["positions"][ticker]["stop_loss"] = new_sl
            _save(p)
            print(f"[session] Trail SHORT {ticker}: SL ${old_sl:.2f} → ${new_sl:.2f} (low ${prev_low:.2f}, +{pnl_pct:.1%})")
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
    ret = None
    if start and start > 0:
        ret = round((spy_price - start) / start * 100, 2)
        p["stats"]["benchmark_return_pct"] = ret
    bi = p.setdefault("benchmark_indices", {})
    bi.setdefault("SPY", {"start_price": None, "current_price": None, "return_pct": None})
    bi["SPY"]["current_price"] = round(spy_price, 2)
    if start:
        bi["SPY"]["start_price"] = round(start, 2)
    if ret is not None:
        bi["SPY"]["return_pct"] = ret
    _save(p)


def set_spy_start_price(spy_price: float) -> None:
    """Record SPY price at session start for benchmark comparison."""
    p = _migrate(_load())
    if not p["stats"].get("spy_start_price"):
        p["stats"]["spy_start_price"] = round(spy_price, 2)
        bi = p.setdefault("benchmark_indices", {})
        bi.setdefault("SPY", {"start_price": None, "current_price": None, "return_pct": None})
        bi["SPY"]["start_price"] = round(spy_price, 2)
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


def update_last_price(ticker: str, price: float) -> None:
    """Update the last known market price for a position (for dashboard unrealized P&L)."""
    p = _migrate(_load())
    if ticker in p["positions"]:
        p["positions"][ticker]["last_price"] = round(price, 2)
        _save(p)


def add_open_order(
    ticker: str,
    qty,
    price: float,
    side: str = "BUY",
    order_type: str = "market",
) -> None:
    """
    Record a proposed order pending Telegram approval.
    status: 'pending' | 'executed' | 'rejected' | 'expired'
    """
    p = _migrate(_load())
    p.setdefault("open_orders", []).append({
        "ticker":       ticker,
        "qty":          qty,
        "side":         side.upper(),
        "price":        round(price, 2),
        "order_type":   order_type,
        "status":       "pending",
        "day":          p["session"].get("current_day", 0),
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    })
    _save(p)
    print(f"[session] Open order queued: {side.upper()} {qty} {ticker} @ ${price:.2f}")


def update_open_order(ticker: str, status: str) -> None:
    """
    Update the most recent pending order for a ticker.
    status: 'executed' | 'rejected' | 'expired'
    """
    p = _migrate(_load())
    for order in reversed(p.get("open_orders", [])):
        if order["ticker"] == ticker and order["status"] == "pending":
            order["status"] = status
            order["resolved_at"] = datetime.now(timezone.utc).isoformat()
            break
    _save(p)
    print(f"[session] Open order {ticker} → {status}")


# ---------------------------------------------------------------------------
# Mid-Cap signal management ($5,000 separate capital pool)
# ---------------------------------------------------------------------------

def _close_signal_generic(p: dict, signals_key: str, capital_key: str,
                           signal_id: str, exit_price: float, exit_date: str,
                           label: str) -> dict:
    """Shared close logic for midcap and penny signal pools."""
    for s in p.get(signals_key, []):
        if s.get("id") == signal_id and s.get("status") == "open":
            entry  = s["entry_price"]
            qty    = s.get("qty", 0)
            exit_p = round(exit_price, 2)

            s["pnl_pct"]    = round((exit_p - entry) / entry * 100, 2) if entry > 0 else 0.0
            s["pnl_usd"]    = round((exit_p - entry) * qty, 2) if qty > 0 else 0.0
            s["exit_price"] = exit_p
            s["exit_date"]  = exit_date
            s["status"]     = "closed"

            if s["pnl_pct"] >= s.get("target_pct", 0):
                s["outcome"] = "win"
            elif s["pnl_pct"] <= -s.get("stop_pct", 0):
                s["outcome"] = "loss"
            elif abs(s["pnl_pct"]) < 0.1:
                s["outcome"] = "breakeven"
            else:
                s["outcome"] = "win" if s["pnl_pct"] > 0 else "loss"

            if qty > 0:
                proceeds = round(exit_p * qty, 2)
                cap = p.get(capital_key, {})
                cap["cash"] = round(cap.get("cash", 0) + proceeds, 2)
                remaining = [
                    sig for sig in p[signals_key]
                    if sig.get("status") == "open" and sig.get("id") != signal_id
                ]
                cap["equity"] = round(
                    cap["cash"] + sum(
                        sig["entry_price"] * sig.get("qty", 0)
                        for sig in remaining
                        if sig.get("entry_price") and sig.get("qty")
                    ), 2
                )
                p[capital_key] = cap

            _save(p)
            print(f"[session] {label} closed: {s['ticker']} {s['pnl_pct']:+.2f}% "
                  f"(${s['pnl_usd']:+.2f}) → {s['outcome']} | equity: ${p[capital_key].get('equity', 0):.0f}")
            return s
    print(f"[session] {label}: signal {signal_id} not found or already closed")
    return {}


def add_midcap_signal(signal: dict) -> None:
    """
    Append a mid-cap signal, allocating from the $5,000 midcap_capital pool.
    Sizes at 50% of available cash, max 4 concurrent.
    """
    from config import PENNY_MAX_CONCURRENT  # noqa — midcap uses same 4 limit
    p = _migrate(_load())
    open_mc = [s for s in p.get("midcap_signals", []) if s.get("status") == "open"]
    if len(open_mc) >= 4:
        print(f"[session] Midcap pool full (4 open), skipping {signal.get('ticker')}")
        return

    cap   = p["midcap_capital"]
    cash  = cap.get("cash", 0)
    entry = signal.get("entry_price", 0)
    if cash <= 0 or entry <= 0:
        print(f"[session] Midcap capital exhausted, skipping {signal.get('ticker')}")
        return

    max_usd   = cash * 0.50
    qty       = max(1, int(max_usd / entry))
    allocated = round(qty * entry, 2)
    if allocated > cash:
        qty       = max(1, int(cash / entry))
        allocated = round(qty * entry, 2)
    if qty < 1:
        print(f"[session] Midcap: insufficient capital for {signal.get('ticker')} @ ${entry:.2f}")
        return

    signal["qty"]           = qty
    signal["allocated_usd"] = allocated
    cap["cash"]   = round(cash - allocated, 2)
    cap["equity"] = round(cap["cash"] + sum(
        s["entry_price"] * s.get("qty", 0) for s in open_mc
        if s.get("entry_price") and s.get("qty")
    ) + allocated, 2)

    p["midcap_signals"].append(signal)
    _save(p)
    print(f"[session] Midcap signal: {signal.get('ticker')} qty={qty} @ ${entry:.2f} "
          f"(${allocated:.0f}) | midcap cash left: ${cap['cash']:.0f}")


def get_open_midcap_signals() -> list:
    p = _migrate(_load())
    return [s for s in p.get("midcap_signals", []) if s.get("status") == "open"]


def close_midcap_signal(signal_id: str, exit_price: float, exit_date: str) -> dict:
    p = _migrate(_load())
    return _close_signal_generic(p, "midcap_signals", "midcap_capital",
                                 signal_id, exit_price, exit_date, "Midcap")


# ---------------------------------------------------------------------------
# Penny stock signal management ($5,000 separate capital pool)
# ---------------------------------------------------------------------------

def add_penny_signal(signal: dict) -> None:
    """
    Append a penny stock signal, allocating from the $5,000 penny_capital pool.
    Sizes at PENNY_MAX_POSITION_PCT (20%) of available cash, max PENNY_MAX_CONCURRENT (5).
    """
    from config import PENNY_MAX_POSITION_PCT, PENNY_MAX_CONCURRENT
    p = _migrate(_load())
    open_penny = [s for s in p.get("penny_signals", []) if s.get("status") == "open"]
    if len(open_penny) >= PENNY_MAX_CONCURRENT:
        print(f"[session] Penny pool full ({PENNY_MAX_CONCURRENT} open), skipping {signal.get('ticker')}")
        return

    cap   = p["penny_capital"]
    cash  = cap.get("cash", 0)
    entry = signal.get("entry_price", 0)
    if cash <= 0 or entry <= 0:
        print(f"[session] Penny capital exhausted, skipping {signal.get('ticker')}")
        return

    max_usd   = cash * PENNY_MAX_POSITION_PCT
    qty       = max(1, int(max_usd / entry))
    allocated = round(qty * entry, 2)
    if allocated > cash:
        qty       = max(1, int(cash / entry))
        allocated = round(qty * entry, 2)
    if qty < 1:
        print(f"[session] Penny: insufficient capital for {signal.get('ticker')} @ ${entry:.2f}")
        return

    signal["qty"]           = qty
    signal["allocated_usd"] = allocated
    cap["cash"]   = round(cash - allocated, 2)
    cap["equity"] = round(cap["cash"] + sum(
        s["entry_price"] * s.get("qty", 0) for s in open_penny
        if s.get("entry_price") and s.get("qty")
    ) + allocated, 2)

    p["penny_signals"].append(signal)
    _save(p)
    print(f"[session] Penny signal: {signal.get('ticker')} qty={qty} @ ${entry:.2f} "
          f"(${allocated:.0f}) | penny cash left: ${cap['cash']:.0f}")


def get_open_penny_signals() -> list:
    p = _migrate(_load())
    return [s for s in p.get("penny_signals", []) if s.get("status") == "open"]


def close_penny_signal(signal_id: str, exit_price: float, exit_date: str) -> dict:
    p = _migrate(_load())
    return _close_signal_generic(p, "penny_signals", "penny_capital",
                                 signal_id, exit_price, exit_date, "Penny")


# ---------------------------------------------------------------------------
# Equity curve & benchmark tracking
# ---------------------------------------------------------------------------

def record_spy_equity(spy_price: float) -> None:
    """Normalize SPY price to $5000 start and append to spy_equity_curve."""
    p = _migrate(_load())
    start = p["stats"].get("spy_start_price")
    if not start or start <= 0:
        return
    normalized = round(5000.0 * (spy_price / start), 2)
    p["spy_equity_curve"].append({
        "day":    p["session"].get("current_day", 0),
        "date":   datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "equity": normalized,
    })
    _save(p)


def record_midcap_equity() -> None:
    """Snapshot current midcap_capital.equity into midcap_equity_curve."""
    p = _migrate(_load())
    equity = p.get("midcap_capital", {}).get("equity", 5000.0)
    p.setdefault("midcap_equity_curve", []).append({
        "day":    p["session"].get("current_day", 0),
        "date":   datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "equity": round(equity, 2),
    })
    _save(p)


def record_penny_equity() -> None:
    """Snapshot current penny_capital.equity into penny_equity_curve."""
    p = _migrate(_load())
    equity = p.get("penny_capital", {}).get("equity", 5000.0)
    p.setdefault("penny_equity_curve", []).append({
        "day":    p["session"].get("current_day", 0),
        "date":   datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "equity": round(equity, 2),
    })
    _save(p)


def update_benchmark_indices(prices: dict) -> None:
    """
    Update QQQ/IWM/GLD benchmark tracking.
    Sets start_price on first call (when start_price is None); updates current_price + return_pct every call.
    prices: {"QQQ": 450.0, "IWM": 200.0, "GLD": 230.0}
    """
    p = _migrate(_load())
    bi = p["benchmark_indices"]
    for ticker, price in prices.items():
        if ticker not in bi:
            bi[ticker] = {"start_price": None, "current_price": None, "return_pct": None}
        entry = bi[ticker]
        if entry["start_price"] is None:
            entry["start_price"] = round(price, 2)
        entry["current_price"] = round(price, 2)
        if entry["start_price"] and entry["start_price"] > 0:
            entry["return_pct"] = round((price - entry["start_price"]) / entry["start_price"] * 100, 2)
    p["benchmark_indices"] = bi
    _save(p)


def set_benchmark_start_prices(prices: dict) -> None:
    """Set only the start_price for benchmark indices (historical anchoring)."""
    p = _migrate(_load())
    bi = p["benchmark_indices"]
    for ticker, price in prices.items():
        if ticker not in bi:
            bi[ticker] = {"start_price": None, "current_price": None, "return_pct": None}
        bi[ticker]["start_price"] = round(price, 2)
    p["benchmark_indices"] = bi
    _save(p)


def update_sector_strength(sector_data: dict) -> None:
    """Save latest sector heatmap to portfolio.json."""
    p = _migrate(_load())
    p["sector_strength"] = sector_data
    _save(p)


def update_strategy_brief(brief: dict) -> None:
    """Save latest strategy consultant brief to portfolio.json."""
    p = _migrate(_load())
    p["strategy_brief"] = brief
    _save(p)


def update_index_etf_signals(signals: dict) -> None:
    """
    Save latest ETF pipeline results.
    signals format: {"SPY": {"action": "hold", "confidence": 0.6}, "QQQ": {"action": "buy", "confidence": 0.8}, ...}
    """
    p = _migrate(_load())
    p["index_etf_signals"] = signals
    _save(p)
