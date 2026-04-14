"""
Market regime detection tool.

Provides:
  get_vix()                     — current VIX value
  get_vix_multiplier()          — (float, label) position sizing multiplier
  get_market_trend(ticker)      — price vs 50/200-day MA
  has_earnings_soon(ticker)     — True if earnings within N days
  get_earnings_calendar(tickers)— {ticker: date} for upcoming earnings
  get_full_regime()             — combined regime snapshot for briefings
  is_fomc_day(date_str)         — True if today is a Fed announcement day
  is_event_blocked(date_str)    — True if any high-impact event blocks trading
  get_premarket_gaps(watchlist) — {ticker: gap_pct} for pre-market gap scanner
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from tools.market_data import get_ohlcv, get_latest_price
from config import VIX_LOW, VIX_MODERATE, VIX_HIGH


# ---------------------------------------------------------------------------
# FOMC & economic calendar — hardcoded Fed announcement dates
# These are the dates when the Fed RELEASES its rate decision (market-moving)
# Source: federalreserve.gov/monetarypolicy/fomccalendars.htm
# ---------------------------------------------------------------------------

FOMC_DATES = {
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    # 2026
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
}

# CPI release dates (high-impact inflation data — avoid trading these days)
# Source: bls.gov/schedule/news_release/cpi.htm
CPI_DATES = {
    # 2025
    "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10",
    "2025-05-13", "2025-06-11", "2025-07-15", "2025-08-12",
    "2025-09-10", "2025-10-15", "2025-11-12", "2025-12-10",
    # 2026
    "2026-01-14", "2026-02-11", "2026-03-11", "2026-04-09",
    "2026-05-13", "2026-06-10", "2026-07-14", "2026-08-12",
    "2026-09-09", "2026-10-14", "2026-11-11", "2026-12-09",
}

# Non-Farm Payroll release dates (first Friday of each month)
NFP_DATES = {
    # 2025
    "2025-01-10", "2025-02-07", "2025-03-07", "2025-04-04",
    "2025-05-02", "2025-06-06", "2025-07-03", "2025-08-01",
    "2025-09-05", "2025-10-03", "2025-11-07", "2025-12-05",
    # 2026
    "2026-01-09", "2026-02-06", "2026-03-06", "2026-04-03",
    "2026-05-01", "2026-06-05", "2026-07-03", "2026-08-07",
    "2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04",
}


def is_fomc_day(date_str: str = None) -> bool:
    """True if date_str is a Fed announcement day."""
    if date_str is None:
        date_str = datetime.today().strftime("%Y-%m-%d")
    return date_str in FOMC_DATES


def is_event_blocked(date_str: str = None) -> tuple:
    """
    Check if today is a high-impact macro event day.
    Returns (blocked: bool, reason: str | None).
    """
    if date_str is None:
        date_str = datetime.today().strftime("%Y-%m-%d")

    if date_str in FOMC_DATES:
        return True, f"FOMC announcement day ({date_str}) — no trades"
    if date_str in CPI_DATES:
        return True, f"CPI release day ({date_str}) — no trades"
    if date_str in NFP_DATES:
        return True, f"Non-Farm Payroll day ({date_str}) — no trades"

    return False, None


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
# Pre-market gap scanner
# ---------------------------------------------------------------------------

def get_premarket_gaps(watchlist: list, gap_threshold: float = 0.02) -> dict:
    """
    Detect pre-market gaps vs prior close for all watchlist tickers.
    A gap is when current price differs from the previous session's close by >= gap_threshold.

    Returns: {ticker: {"gap_pct": float, "prev_close": float, "current": float, "flagged": bool}}
    """
    results = {}
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=7)).strftime("%Y-%m-%d")

    for ticker in watchlist:
        try:
            bars = get_ohlcv(ticker, start, end)
            if len(bars) < 2:
                results[ticker] = {"error": "insufficient data"}
                continue

            prev_close = bars[-2]["close"]   # yesterday's closing price
            current    = get_latest_price(ticker)  # pre-market or last price
            gap_pct    = (current - prev_close) / prev_close

            results[ticker] = {
                "prev_close": round(prev_close, 4),
                "current":    round(current, 4),
                "gap_pct":    round(gap_pct * 100, 2),
                "flagged":    abs(gap_pct) >= gap_threshold,
            }
        except Exception as e:
            results[ticker] = {"error": str(e)}

    return results


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

    # Check macro event calendar for the week ahead
    today = datetime.today()
    week_events = []
    for i in range(7):
        d = (today + timedelta(days=i)).strftime("%Y-%m-%d")
        blocked, reason = is_event_blocked(d)
        if blocked:
            week_events.append({"date": d, "event": reason})

    # Weekend crypto performance (last 2 days)
    crypto_perf = {}
    for c in ["BTC-USD", "ETH-USD", "SOL-USD"]:
        try:
            end   = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")
            start = (datetime.today() - timedelta(days=7)).strftime("%Y-%m-%d")
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
        "week_events":    week_events,
    }
