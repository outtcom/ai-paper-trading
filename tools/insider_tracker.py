"""
Compute insider activity signals from Finnhub Form 4 transaction data.
No LLM calls — pure deterministic signal extraction.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from tools.finnhub_data import get_insider_transactions

SIGNIFICANT_DOLLAR_THRESHOLD = 100_000
STRONG_BUY_THRESHOLD  =  500_000
BUY_THRESHOLD         =  100_000
SELL_THRESHOLD        = -100_000
STRONG_SELL_THRESHOLD = -500_000


def _signal_strength(net_dollar_value: float, net_buy_count: int) -> str:
    cluster_bonus = net_buy_count >= 3
    if net_dollar_value >= STRONG_BUY_THRESHOLD or (net_dollar_value >= BUY_THRESHOLD and cluster_bonus):
        return "strong_buy"
    if net_dollar_value >= BUY_THRESHOLD:
        return "buy"
    if net_dollar_value <= STRONG_SELL_THRESHOLD:
        return "strong_sell"
    if net_dollar_value <= SELL_THRESHOLD:
        return "sell"
    return "neutral"


def compute_insider_signal(ticker: str, days_back: int = 60) -> dict:
    """
    Aggregate insider transactions into a structured signal dict.
    Returns signal_strength: strong_buy | buy | neutral | sell | strong_sell.
    """
    try:
        transactions = get_insider_transactions(ticker)
        cutoff = datetime.utcnow() - timedelta(days=days_back)

        net_buy_count = 0
        net_sell_count = 0
        net_dollar_value = 0.0
        largest = None
        notable = []

        for t in transactions:
            date_str = t.get("date", "")
            if not date_str:
                continue
            try:
                txn_date = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue
            if txn_date < cutoff:
                continue

            shares = abs(t.get("shares", 0) or 0)
            price = t.get("price", 0) or 0
            change = t.get("change", 0) or 0
            dollar_value = shares * price
            direction = "buy" if change > 0 else "sell"

            if direction == "buy":
                net_buy_count += 1
                net_dollar_value += dollar_value
            else:
                net_sell_count += 1
                net_dollar_value -= dollar_value

            txn_record = {
                "name": t.get("name", ""),
                "direction": direction,
                "dollar_value": round(dollar_value, 2),
                "date": date_str,
                "shares": shares,
                "price": price,
            }

            if largest is None or dollar_value > largest["dollar_value"]:
                largest = txn_record

            if dollar_value >= SIGNIFICANT_DOLLAR_THRESHOLD:
                notable.append(txn_record)

        return {
            "ticker": ticker,
            "days_back": days_back,
            "transactions_analyzed": net_buy_count + net_sell_count,
            "net_buy_count": net_buy_count,
            "net_sell_count": net_sell_count,
            "net_dollar_value": round(net_dollar_value, 2),
            "largest_single_transaction": largest,
            "notable_insiders": notable,
            "signal_strength": _signal_strength(net_dollar_value, net_buy_count),
            "error": None,
        }

    except Exception as e:
        return {
            "ticker": ticker,
            "days_back": days_back,
            "transactions_analyzed": 0,
            "net_buy_count": 0,
            "net_sell_count": 0,
            "net_dollar_value": 0.0,
            "largest_single_transaction": None,
            "notable_insiders": [],
            "signal_strength": "neutral",
            "error": str(e),
        }
