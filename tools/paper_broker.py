"""
Simulated paper trading broker.
Tracks virtual portfolio in .tmp/portfolio.json — no brokerage account required.
Price data comes from yfinance.

When you are ready to trade with real money, swap this module for an IBKR or
Questrade API integration without changing any other code.
"""
import json
import os
from datetime import datetime
from typing import Optional
from tools.market_data import get_latest_price

PORTFOLIO_FILE = os.path.join(".tmp", "portfolio.json")


def _load_portfolio() -> dict:
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE) as f:
            return json.load(f)
    # Default starting portfolio
    from config import INITIAL_CAPITAL
    return {
        "cash": float(INITIAL_CAPITAL),
        "equity": float(INITIAL_CAPITAL),
        "positions": {},
        "trade_history": [],
        "created_at": datetime.utcnow().isoformat(),
    }


def _save_portfolio(portfolio: dict) -> None:
    os.makedirs(".tmp", exist_ok=True)
    portfolio["updated_at"] = datetime.utcnow().isoformat()
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)


def get_portfolio() -> dict:
    """Return current portfolio state with mark-to-market equity."""
    p = _load_portfolio()
    total_value = p["cash"]
    positions_list = []

    for ticker, pos in p.get("positions", {}).items():
        try:
            price = get_latest_price(ticker)
            market_value = pos["shares"] * price
            unrealized_pl = market_value - (pos["avg_cost"] * pos["shares"])
            total_value += market_value
            positions_list.append({
                "ticker": ticker,
                "qty": pos["shares"],
                "avg_entry": pos["avg_cost"],
                "market_value": round(market_value, 2),
                "unrealized_pl": round(unrealized_pl, 2),
                "current_price": price,
            })
        except Exception:
            positions_list.append({"ticker": ticker, **pos})

    p["equity"] = round(total_value, 2)
    return {
        "cash": round(p["cash"], 2),
        "equity": round(total_value, 2),
        "positions": positions_list,
    }


def submit_order(ticker: str, action: str, qty: int, price: Optional[float] = None) -> dict:
    """
    Simulate a paper trade order.
    action: 'buy' | 'sell' | 'hold'
    price: execution price (uses latest market price if None)
    """
    if action == "hold" or qty <= 0:
        return {"status": "skipped", "reason": "HOLD or zero qty"}

    from config import SLIPPAGE_PCT
    p = _load_portfolio()
    exec_price = price or get_latest_price(ticker)
    # Realistic fill: buys execute slightly above mid, sells slightly below
    if action == "buy":
        exec_price = round(exec_price * (1 + SLIPPAGE_PCT), 4)
    elif action == "sell":
        exec_price = round(exec_price * (1 - SLIPPAGE_PCT), 4)

    if action == "buy":
        cost = exec_price * qty
        if cost > p["cash"]:
            qty = int(p["cash"] / exec_price)
            cost = exec_price * qty
        if qty <= 0:
            return {"status": "rejected", "reason": "Insufficient cash"}

        p["cash"] -= cost
        positions = p.setdefault("positions", {})
        if ticker in positions:
            total_shares = positions[ticker]["shares"] + qty
            total_cost = positions[ticker]["avg_cost"] * positions[ticker]["shares"] + cost
            positions[ticker] = {"shares": total_shares, "avg_cost": round(total_cost / total_shares, 4)}
        else:
            positions[ticker] = {"shares": qty, "avg_cost": exec_price}

    elif action == "sell":
        positions = p.get("positions", {})
        if ticker not in positions or positions[ticker]["shares"] < qty:
            qty = positions.get(ticker, {}).get("shares", 0)
        if qty <= 0:
            return {"status": "rejected", "reason": "No shares to sell"}
        proceeds = exec_price * qty
        p["cash"] += proceeds
        positions[ticker]["shares"] -= qty
        if positions[ticker]["shares"] == 0:
            del positions[ticker]

    order = {
        "order_id": f"SIM-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "ticker": ticker,
        "action": action,
        "qty": qty,
        "exec_price": exec_price,
        "slippage_pct": SLIPPAGE_PCT,
        "total_value": round(exec_price * qty, 2),
        "status": "filled",
        "timestamp": datetime.utcnow().isoformat(),
    }
    p.setdefault("trade_history", []).append(order)
    _save_portfolio(p)
    return order


def calculate_qty(ticker: str, portfolio_fraction: float, price: float, cash: float) -> int:
    """Calculate share quantity given a target fraction of cash to deploy."""
    target_value = cash * portfolio_fraction
    qty = int(target_value / price)
    return max(qty, 0)


def reset_portfolio() -> None:
    """Reset portfolio to starting state (for new backtest runs)."""
    if os.path.exists(PORTFOLIO_FILE):
        os.remove(PORTFOLIO_FILE)
    _load_portfolio()  # creates fresh file
    print("Portfolio reset to initial state.")


if __name__ == "__main__":
    portfolio = get_portfolio()
    print(json.dumps(portfolio, indent=2))
