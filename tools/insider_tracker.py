"""
Compute insider activity signals from Finnhub Form 4 transaction data.
No LLM calls — pure deterministic signal extraction.

Finnhub field mapping (important):
  t["change"] = shares actually bought/sold in this transaction (signed: + buy, - sell)
  t["share"]  = total shares held by insider AFTER the transaction (not useful here)
  t["price"]  = transaction price per share

Dollar value = abs(change) * price  (NOT shares * price)
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from tools.finnhub_data import get_insider_transactions

# Thresholds for meaningful open-market transactions
SIGNIFICANT_DOLLAR_THRESHOLD =    50_000   # floor for "notable" transaction
STRONG_BUY_THRESHOLD         =   500_000   # net open-market buy > $500K → strong_buy
BUY_THRESHOLD                =    50_000   # net open-market buy > $50K → buy
SELL_THRESHOLD               =   -50_000   # net open-market sell > $50K → sell
STRONG_SELL_THRESHOLD        =  -500_000   # net open-market sell > $500K → strong_sell

# Sanity cap: a single transaction above this is likely data noise (no insider
# legitimately trades >$500M of stock in a single Form 4 event)
MAX_SINGLE_TXN = 500_000_000


def _fmt(value: float) -> str:
    """Human-readable dollar amount."""
    v = abs(value)
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


def _signal_strength(net_open_buy: float, net_buy_count: int) -> str:
    """Only count open-market transactions (price > 0) for signal."""
    cluster_bonus = net_buy_count >= 3
    if net_open_buy >= STRONG_BUY_THRESHOLD or (net_open_buy >= BUY_THRESHOLD and cluster_bonus):
        return "strong_buy"
    if net_open_buy >= BUY_THRESHOLD:
        return "buy"
    if net_open_buy <= STRONG_SELL_THRESHOLD:
        return "strong_sell"
    if net_open_buy <= SELL_THRESHOLD:
        return "sell"
    return "neutral"


def compute_insider_signal(ticker: str, days_back: int = 60) -> dict:
    """
    Aggregate insider transactions into a structured signal dict.

    Key design decisions:
    - Uses abs(change) for transaction size, NOT t["share"] (total held post-txn)
    - Zero-price transactions (RSU vests, option grants) are counted but NOT included
      in dollar totals — they're compensation, not conviction signals
    - Transactions above MAX_SINGLE_TXN are capped to filter Finnhub data anomalies
    - Signal is based only on open-market transactions (price > 0)
    """
    try:
        transactions = get_insider_transactions(ticker)
        cutoff = datetime.utcnow() - timedelta(days=days_back)

        net_buy_count   = 0
        net_sell_count  = 0
        net_open_buy    = 0.0   # dollar net of open-market only (price > 0)
        grant_count     = 0     # zero-price events (RSU / option grants)
        largest         = None
        notable         = []

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

            # change is signed: positive = buy, negative = sell
            change    = t.get("change", 0) or 0
            shares_txn = abs(change)
            price      = t.get("price", 0) or 0
            direction  = "buy" if change > 0 else "sell"

            # Zero-price: RSU vest, stock grant, option exercise at $0 — not a market signal
            if price == 0:
                grant_count += 1
                continue

            dollar_value = min(shares_txn * price, MAX_SINGLE_TXN)

            if direction == "buy":
                net_buy_count  += 1
                net_open_buy   += dollar_value
            else:
                net_sell_count += 1
                net_open_buy   -= dollar_value

            txn_record = {
                "name":        t.get("name", ""),
                "direction":   direction,
                "dollar_value": round(dollar_value, 2),
                "dollar_fmt":  _fmt(dollar_value),
                "date":        date_str,
                "shares_traded": shares_txn,
                "price_per_share": price,
            }

            if largest is None or dollar_value > largest["dollar_value"]:
                largest = txn_record

            if dollar_value >= SIGNIFICANT_DOLLAR_THRESHOLD:
                notable.append(txn_record)

        return {
            "ticker":                   ticker,
            "days_back":               days_back,
            "open_market_buys":        net_buy_count,
            "open_market_sells":       net_sell_count,
            "compensation_grants":     grant_count,
            "net_open_market_value":   round(net_open_buy, 2),
            "net_open_market_fmt":     (("+" if net_open_buy >= 0 else "-") + _fmt(net_open_buy)),
            "largest_single_transaction": largest,
            "notable_transactions":    notable,
            "signal_strength":         _signal_strength(net_open_buy, net_buy_count),
            "error": None,
        }

    except Exception as e:
        return {
            "ticker":                   ticker,
            "days_back":               days_back,
            "open_market_buys":        0,
            "open_market_sells":       0,
            "compensation_grants":     0,
            "net_open_market_value":   0.0,
            "net_open_market_fmt":     "$0",
            "largest_single_transaction": None,
            "notable_transactions":    [],
            "signal_strength":         "neutral",
            "error": str(e),
        }
