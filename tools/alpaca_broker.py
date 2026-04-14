"""
Submit and manage paper trades via Alpaca.
PAPER TRADING ONLY — always uses the paper-api endpoint.
"""
import os
from dotenv import load_dotenv

load_dotenv()

def _get_api():
    import alpaca_trade_api as tradeapi
    return tradeapi.REST(
        os.environ["ALPACA_API_KEY"],
        os.environ["ALPACA_SECRET_KEY"],
        "https://paper-api.alpaca.markets",  # always paper, never live
    )


def get_portfolio() -> dict:
    """Return current portfolio: cash, equity, positions."""
    api = _get_api()
    account = api.get_account()
    positions = api.list_positions()
    return {
        "cash": float(account.cash),
        "equity": float(account.equity),
        "positions": [
            {
                "ticker": p.symbol,
                "qty": int(p.qty),
                "avg_entry": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
            }
            for p in positions
        ],
    }


def submit_order(ticker: str, action: str, qty: int) -> dict:
    """
    Submit a paper trade order.
    action: 'buy' | 'sell' | 'hold'
    Returns order confirmation dict.
    """
    if action == "hold" or qty <= 0:
        return {"status": "skipped", "reason": "HOLD or zero qty"}

    api = _get_api()
    side = "buy" if action == "buy" else "sell"
    order = api.submit_order(
        symbol=ticker,
        qty=qty,
        side=side,
        type="market",
        time_in_force="day",
    )
    return {
        "order_id": order.id,
        "ticker": ticker,
        "action": side,
        "qty": qty,
        "status": order.status,
    }


def calculate_qty(ticker: str, portfolio_fraction: float, price: float, cash: float) -> int:
    """
    Calculate how many shares to buy given a target portfolio fraction.
    Returns 0 if not enough cash.
    """
    target_value = cash * portfolio_fraction
    qty = int(target_value / price)
    return max(qty, 0)


if __name__ == "__main__":
    portfolio = get_portfolio()
    print("Portfolio:", portfolio)
