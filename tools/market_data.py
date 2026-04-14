from typing import Dict, List
"""
Market data tool — fetches OHLCV stock data using yfinance.
No API key required. Works globally including Canada.

Drop-in replacement for alpaca_data.py with the same interface.
When upgrading to a paid data provider (Polygon.io, IBKR, etc.), only this file changes.
"""
from datetime import datetime, timedelta


def get_ohlcv(ticker: str, start: str, end: str) -> List[Dict]:
    """
    Fetch daily OHLCV bars for a ticker between start and end (YYYY-MM-DD).
    Returns list of dicts: [{date, open, high, low, close, volume}, ...]
    """
    import yfinance as yf
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if df.empty:
        return []
    result = []
    for ts, row in df.iterrows():
        result.append({
            "date": ts.strftime("%Y-%m-%d"),
            "open": round(float(row["Open"]), 4),
            "high": round(float(row["High"]), 4),
            "low": round(float(row["Low"]), 4),
            "close": round(float(row["Close"]), 4),
            "volume": int(row["Volume"]),
        })
    return result


def get_latest_price(ticker: str) -> float:
    """Return the most recent closing price for a ticker."""
    end = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=7)).strftime("%Y-%m-%d")
    bars = get_ohlcv(ticker, start, end)
    if bars:
        return bars[-1]["close"]
    raise ValueError(f"No price data returned for {ticker}")


if __name__ == "__main__":
    bars = get_ohlcv("AAPL", "2024-01-02", "2024-01-10")
    for b in bars:
        print(b)
    print(f"Latest AAPL price: ${get_latest_price('AAPL'):.2f}")
