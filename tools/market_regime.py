"""
Market regime detection tool.

Provides:
  get_vix()                     — current VIX value
  get_vix_multiplier()          — (float, label) position sizing multiplier
  get_market_trend(ticker)      — price vs 50/200-day MA
  has_earnings_soon(ticker)     — True if earnings within N days
  get_earnings_calendar(tickers)— {ticker: date} for upcoming earnings
  get_full_regime()             — combined regime snapshot for briefings
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from tools.market_data import get_ohlcv, get_latest_price
from config import VIX_LOW, VIX_MODERATE, VIX_HIGH


# ---------------------------------------------------------------------------
# VIX — volatility regime
# ---------------------------------------------------------------------------

def get_vix() -> float:
    """Return current VIX level."""
    return get_latest_price("^VIX")


def get_vix_multiplier() -> tuple:
    """
    Return (size_multiplier, label) based on current VIX.
    Multiplier applies to position sizing in morning_session.
    """
    try:
        vix = get_vix()
    except Exception:
        return 0.75, "VIX unavailable — defaulting to 75% sizing"

    if vix < VIX_LOW:
        return 1.0,  f"LOW ({vix:.1f}) ✅ — full position sizing"
    elif vix < VIX_MODERATE:
        return 0.75, f"MODERATE ({vix:.1f}) ⚠️ — 75% position sizing"
    elif vix < VIX_HIGH:
        return 0.5,  f"HIGH ({vix:.1f}) 🔴 — 50% position sizing"
    else:
        return 0.0,  f"EXTREME ({vix:.1f}) 🚨 — no trades today"


# ---------------------------------------------------------------------------
# Market trend — SPY / QQQ vs moving averages
# ---------------------------------------------------------------------------

def get_market_trend(ticker: str = "SPY") -> dict:
    """
    Check if ticker is above/below its 50-day and 200-day MA.
    Returns dict with price, MA values, and trend labels.
    """
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=220)).strftime("%Y-%m-%d")

    try:
        bars = get_ohlcv(ticker, start, end)
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}

    if len(bars) < 10:
        return {"ticker": ticker, "error": "Insufficient data"}

    closes  = [b["close"] for b in bars]
    current = closes[-1]

    ma50  = round(sum(closes[-50:]) / min(50, len(closes)), 2)
    ma200 = round(sum(closes[-200:]) / min(200, len(closes)), 2) if len(closes) >= 30 else None

    above50  = current > ma50
    above200 = (current > ma200) if ma200 else None

    trend = "BULLISH" if above50 and (above200 is not False) else \
            "BEARISH" if not above50 else "MIXED"

    return {
        "ticker":      ticker,
        "price":       round(current, 2),
        "ma50":        ma50,
        "above_ma50":  above50,
        "pct_vs_ma50": round((current - ma50) / ma50 * 100, 2),
        "ma200":       ma200,
        "above_ma200": above200,
        "trend":       trend,
    }


# ---------------------------------------------------------------------------
# Earnings calendar
# ---------------------------------------------------------------------------

def has_earnings_soon(ticker: str, days: int = 5) -> dict:
    """
    Return {'has_earnings': bool, 'date': str|None, 'days_until': int|None}.
    Always returns False for crypto tickers.
    """
    if "-USD" in ticker:
        return {"has_earnings": False, "date": None, "days_until": None}

    try:
        import yfinance as yf
        t   = yf.Ticker(ticker)
        now = datetime.now()

        # Try earnings_dates (newer yfinance)
        try:
            ed = t.earnings_dates
            if ed is not None and not ed.empty:
                for idx in ed.index:
                    dt = idx.to_pydatetime().replace(tzinfo=None)
                    diff = (dt - now).days
                    if 0 <= diff <= days:
                        return {"has_earnings": True, "date": dt.strftime("%Y-%m-%d"), "days_until": diff}
        except Exception:
            pass

        # Fallback: calendar dict
        try:
            cal = t.calendar
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date", [])
                if not isinstance(dates, list):
                    dates = [dates]
                for d in dates:
                    if hasattr(d, "to_pydatetime"):
                        d = d.to_pydatetime()
                    if hasattr(d, "replace"):
                        d = d.replace(tzinfo=None)
                        diff = (d - now).days
                        if 0 <= diff <= days:
                            return {"has_earnings": True, "date": d.strftime("%Y-%m-%d"), "days_until": diff}
        except Exception:
            pass

    except Exception:
        pass

    return {"has_earnings": False, "date": None, "days_until": None}


def get_earnings_calendar(tickers: list, days: int = 7) -> dict:
    """
    Check all tickers for upcoming earnings within `days` trading days.
    Returns {ticker: {'has_earnings': bool, 'date': str, 'days_until': int}}.
    """
    return {ticker: has_earnings_soon(ticker, days) for ticker in tickers}


# ---------------------------------------------------------------------------
# Full regime snapshot (used by weekly briefing)
# ---------------------------------------------------------------------------

def get_full_regime(watchlist: list) -> dict:
    """
    Returns a comprehensive market regime snapshot:
      vix, vix_multiplier, spy_trend, qqq_trend, btc_trend, earnings
    """
    vix_mult, vix_label = get_vix_multiplier()

    try:
        vix_val = get_vix()
    except Exception:
        vix_val = None

    spy = get_market_trend("SPY")
    qqq = get_market_trend("QQQ")
    btc = get_market_trend("BTC-USD")

    # Weekend crypto performance (last 2 days)
    crypto_perf = {}
    for c in ["BTC-USD", "ETH-USD", "SOL-USD"]:
        try:
            end   = datetime.today().strftime("%Y-%m-%d")
            start = (datetime.today() - timedelta(days=3)).strftime("%Y-%m-%d")
            bars  = get_ohlcv(c, start, end)
            if len(bars) >= 2:
                pct = round((bars[-1]["close"] - bars[-2]["close"]) / bars[-2]["close"] * 100, 2)
                crypto_perf[c] = {"price": bars[-1]["close"], "pct_1d": pct}
        except Exception:
            crypto_perf[c] = {}

    earnings = get_earnings_calendar(
        [t for t in watchlist if "-USD" not in t], days=7
    )

    return {
        "vix":            vix_val,
        "vix_label":      vix_label,
        "vix_multiplier": vix_mult,
        "spy":            spy,
        "qqq":            qqq,
        "btc":            btc,
        "crypto_perf":    crypto_perf,
        "earnings":       earnings,
    }
