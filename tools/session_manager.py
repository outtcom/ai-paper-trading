"""
Session manager for the 10-day paper trading session.
Manages all portfolio state in docs/portfolio.json, which is:
  - tracked by git (committed after each GitHub Actions run)
  - served by GitHub Pages (powers the live dashboard)

This module is the single source of truth for session state.
The existing paper_broker.py is NOT used by the session — we manage state directly here.
"""
import json
import os
from datetime import datetime, timezone

# Absolute path to docs/portfolio.json, resolved relative to this file's location
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_SYSTEM_DIR = os.path.dirname(_TOOLS_DIR)
PORTFOLIO_FILE = os.path.join(_SYSTEM_DIR, "docs", "portfolio.json")

TOTAL_DAYS = 10
INITIAL_CAPITAL = 5_000.0


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
        "positions": {},
        "trade_history": [],
        "equity_curve": [],
    }


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(PORTFOLIO_FILE), exist_ok=True)
    data["session"]["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_portfolio() -> dict:
    """Return the full portfolio state dict."""
    return _load()


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


def record_equity(equity: float) -> None:
    """Append today's closing equity to the equity curve."""
    p = _load()
    p["equity"] = round(equity, 2)
    p["equity_curve"].append({
        "day": p["session"].get("current_day", 0),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "equity": round(equity, 2),
    })
    _save(p)


def open_position(
    ticker: str,
    qty: int,
    entry_price: float,
    stop_loss_pct: float,
    take_profit_pct: float,
) -> None:
    """
    Record a newly opened long position and deduct cash.
    stop_loss_pct / take_profit_pct are fractions (e.g. 0.03 = 3%).
    """
    p = _load()
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

    p["cash"] = round(p["cash"] - cost, 2)
    p["positions"][ticker] = {
        "qty": qty,
        "entry_price": round(entry_price, 2),
        "cost_basis": cost,
        "stop_loss": sl_price,
        "take_profit": tp_price,
        "stop_loss_pct": round(stop_loss_pct * 100, 2),
        "take_profit_pct": round(take_profit_pct * 100, 2),
        "opened_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    _save(p)
    print(f"[session] Opened {ticker}: {qty} shares @ ${entry_price:.2f}  TP=${tp_price:.2f}  SL=${sl_price:.2f}")


def close_position(ticker: str, exit_price: float, reason: str) -> dict:
    """
    Close an open position at exit_price and add proceeds to cash.
    reason: 'take_profit' | 'stop_loss' | 'manual'
    Returns a trade record dict (also appended to trade_history).
    """
    p = _load()
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
    }

    p["cash"] = round(p["cash"] + proceeds, 2)
    del p["positions"][ticker]
    p["trade_history"].append(trade)
    _save(p)
    print(f"[session] Closed {ticker} @ ${exit_price:.2f}  P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)  Reason: {reason}")
    return trade
