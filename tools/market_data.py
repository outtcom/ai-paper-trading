"""
Market data tool — fetches OHLCV and quote data.

Primary source:  Finnhub (API key in .env — reliable on cloud runners)
Fallback source: yfinance (free, but Yahoo Finance rate-limits cloud IPs)

Interface is identical — callers don't need to change.
When upgrading to Polygon.io or IBKR, only this file changes.
"""
import os
import time
import urllib.request
import json
from datetime import datetime, timedelta
from typing import List, Dict


# ---------------------------------------------------------------------------
# Finnhub helpers
# ---------------------------------------------------------------------------

CRYPTO_SYMBOLS = {
    "BTC-USD": "BINANCE:BTCUSDT",
    "ETH-USD": "BINANCE:ETHUSDT",
    "SOL-USD": "BINANCE:SOLUSDT",
}

# Tickers that are indices (use Finnhub quote, not candle)
INDEX_TICKERS = {"^VIX", "^GSPC", "^DJI", "^IXIC"}


def _finnhub_get(path: str) -> dict:
    """Make a Finnhub API call. Returns parsed JSON or empty dict on failure."""
    api_key = os.getenv("FINNHUB_API_KEY", "")
    if not api_key:
        return {}
    url = f"https://finnhub.io/api/v1{path}&token={api_key}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def _finnhub_ohlcv(ticker: str, start_ts: int, end_ts: int) -> List[Dict]:
    """
    Fetch daily OHLCV from Finnhub for a stock/ETF or crypto ticker.
    Returns list of bar dicts or [] on failure.
    """
    if ticker in CRYPTO_SYMBOLS:
        symbol = CRYPTO_SYMBOLS[ticker]
        data = _finnhub_get(f"/crypto/candle?symbol={symbol}&resolution=D&from={start_ts}&to={end_ts}")
    else:
        data = _finnhub_get(f"/stock/candle?symbol={ticker}&resolution=D&from={start_ts}&to={end_ts}")

    if data.get("s") != "ok":
        return []

    timestamps = data.get("t", [])
    closes     = data.get("c", [])
    opens      = data.get("o", [])
    highs      = data.get("h", [])
    lows       = data.get("l", [])
    volumes    = data.get("v", [])

    bars = []
    for i, ts in enumerate(timestamps):
        bars.append({
            "date":   datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d"),
            "open":   round(float(opens[i]), 4),
            "high":   round(float(highs[i]), 4),
            "low":    round(float(lows[i]), 4),
            "close":  round(float(closes[i]), 4),
            "volume": int(volumes[i]) if i < len(volumes) else 0,
        })
    return bars


def _finnhub_latest_price(ticker: str):
    """Return latest price from Finnhub quote endpoint, or None."""
    if ticker in CRYPTO_SYMBOLS:
        # Use crypto candle — last bar close
        end_ts   = int(time.time())
        start_ts = end_ts - 2 * 24 * 3600
        bars = _finnhub_ohlcv(ticker, start_ts, end_ts)
        return bars[-1]["close"] if bars else None

    # For indices like ^VIX, strip the ^
    symbol = ticker.lstrip("^")
    data = _finnhub_get(f"/quote?symbol={symbol}")
    price = data.get("c")
    return float(price) if price and float(price) > 0 else None


# ---------------------------------------------------------------------------
# yfinance fallback
# ---------------------------------------------------------------------------

def _yf_ohlcv(ticker: str, start: str, end: str) -> List[Dict]:
    """Fetch OHLCV from yfinance. Returns [] on any failure."""
    try:
        import yfinance as yf
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty:
            return []
        result = []
        for ts, row in df.iterrows():
            result.append({
                "date":   ts.strftime("%Y-%m-%d"),
                "open":   round(float(row["Open"]), 4),
                "high":   round(float(row["High"]), 4),
                "low":    round(float(row["Low"]), 4),
                "close":  round(float(row["Close"]), 4),
                "volume": int(row["Volume"]),
            })
        return result
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def get_ohlcv(ticker: str, start: str, end: str) -> List[Dict]:
    """
    Fetch daily OHLCV bars for a ticker between start and end (YYYY-MM-DD).
    Returns list of dicts: [{date, open, high, low, close, volume}, ...]
    Tries Finnhub first, falls back to yfinance.
    """
    # Convert date strings to unix timestamps for Finnhub
    start_ts = int(datetime.strptime(start, "%Y-%m-%d").timestamp())
    end_ts   = int(datetime.strptime(end,   "%Y-%m-%d").timestamp()) + 86400  # inclusive

    # Indices not supported via candle on Finnhub free tier — go straight to yfinance
    if ticker not in INDEX_TICKERS:
        bars = _finnhub_ohlcv(ticker, start_ts, end_ts)
        if bars:
            return bars

    # Fallback: yfinance
    return _yf_ohlcv(ticker, start, end)


def get_latest_price(ticker: str) -> float:
    """
    Return the most recent closing price for a ticker.
    Tries Finnhub first, falls back to yfinance historical bars.
    """
    # Try Finnhub quote (fastest path)
    price = _finnhub_latest_price(ticker)
    if price is not None:
        return price

    # Fallback: last bar from OHLCV
    end   = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=10)).strftime("%Y-%m-%d")
    bars  = get_ohlcv(ticker, start, end)
    if bars:
        return bars[-1]["close"]

    raise ValueError(f"No price data returned for {ticker} (tried Finnhub + yfinance)")


if __name__ == "__main__":
    print(f"VIX:  {get_latest_price('^VIX'):.2f}")
    print(f"SPY:  {get_latest_price('SPY'):.2f}")
    print(f"BTC:  {get_latest_price('BTC-USD'):,.0f}")
    bars = get_ohlcv("AAPL", "2024-01-02", "2024-01-10")
    for b in bars:
        print(b)
