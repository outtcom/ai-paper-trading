from typing import Dict, List
"""
Fetch historical OHLCV stock data from Alpaca Markets.
Uses yfinance as a fallback if Alpaca is unavailable.
"""
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

def get_ohlcv(ticker: str, start: str, end: str) -> List[Dict]:
    """
    Fetch daily OHLCV bars for a ticker between start and end (YYYY-MM-DD).
    Returns a list of dicts: [{date, open, high, low, close, volume}, ...]
    """
    try:
        import alpaca_trade_api as tradeapi
        api = tradeapi.REST(
            os.environ["ALPACA_API_KEY"],
            os.environ["ALPACA_SECRET_KEY"],
            os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
        )
        bars = api.get_bars(ticker, "1Day", start=start, end=end).df
        result = []
        for ts, row in bars.iterrows():
            result.append({
                "date": ts.strftime("%Y-%m-%d"),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
            })
        return result
    except Exception as e:
        print(f"[alpaca_data] Alpaca failed ({e}), falling back to yfinance")
        return _yfinance_fallback(ticker, start, end)


def _yfinance_fallback(ticker: str, start: str, end: str) -> List[Dict]:
    import yfinance as yf
    df = yf.download(ticker, start=start, end=end, progress=False)
    result = []
    for ts, row in df.iterrows():
        result.append({
            "date": ts.strftime("%Y-%m-%d"),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": int(row["Volume"]),
        })
    return result


def get_latest_price(ticker: str) -> float:
    """Return the most recent closing price for a ticker."""
    end = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=5)).strftime("%Y-%m-%d")
    bars = get_ohlcv(ticker, start, end)
    if bars:
        return bars[-1]["close"]
    raise ValueError(f"No price data returned for {ticker}")


if __name__ == "__main__":
    bars = get_ohlcv("AAPL", "2024-01-02", "2024-01-10")
    for b in bars:
        print(b)
