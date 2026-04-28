"""
Universe Scanner — scans a broad S&P 500 proxy universe and returns the top
momentum leaders per GICS sector.

Used at ~7:30 AM ET in the pre-market pipeline to dynamically select which
tickers to analyse each session, rather than relying solely on the small
static SECTOR_MAP in config.py.

Scoring formula (higher = stronger momentum):
    score = 0.5 * mom_5d + 0.3 * mom_20d + 0.2 * mom_60d

Reversal filter:
    Exclude any ticker where mom_10d > 15%  (parabolic — mean-reversion risk)

Fallback:
    If ALL tickers in a sector fail data retrieval, fall back to the static
    SECTOR_MAP in config.py and log the reason.
"""

from __future__ import annotations

import sys
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Path fix — allow running from repo root OR tools/ directory
# ---------------------------------------------------------------------------
_THIS_DIR   = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT  = os.path.dirname(_THIS_DIR)
for _p in (_REPO_ROOT, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.market_data import get_ohlcv  # noqa: E402
from config import SECTOR_MAP            # noqa: E402

# ---------------------------------------------------------------------------
# Broad S&P 500 proxy universe  (~140 large-cap tickers, 11 GICS sectors)
# GLD is an ETF handled separately by the main pipeline — not included here.
# ---------------------------------------------------------------------------

SP500_UNIVERSE: Dict[str, List[str]] = {
    "Technology": [
        "AAPL", "NVDA", "MSFT", "AVGO", "ORCL",
        "AMD",  "CSCO", "QCOM", "AMAT", "MU",
        "ADI",  "KLAC", "TXN",  "LRCX", "NOW",
    ],
    "Communication Services": [
        "GOOGL", "META", "NFLX", "DIS", "T",
        "TMUS",  "VZ",   "EA",   "TTWO", "WBD",
        "UBER",  "SNAP", "OMC",
    ],
    "Consumer Discretionary": [
        "AMZN", "TSLA", "HD",   "MCD",  "NKE",
        "LOW",  "TJX",  "BKNG", "CMG",  "SBUX",
        "ORLY", "EBAY",
    ],
    "Healthcare": [
        "LLY",  "UNH",  "JNJ",  "MRK",  "ABBV",
        "TMO",  "ABT",  "DHR",  "AMGN", "ISRG",
        "CVS",  "CI",   "HCA",
    ],
    "Financials": [
        "JPM",  "V",    "MA",   "BAC",  "WFC",
        "GS",   "MS",   "BLK",  "AXP",  "SCHW",
        "C",    "PGR",
    ],
    "Energy": [
        "XOM",  "CVX",  "COP",  "EOG",  "SLB",
        "MPC",  "VLO",  "PSX",  "OXY",  "DVN",
        "HAL",  "KMI",
    ],
    "Industrials": [
        "CAT",  "GE",   "RTX",  "HON",  "UPS",
        "BA",   "LMT",  "DE",   "ETN",  "MMM",
        "FDX",  "CSX",  "NSC",
    ],
    "Consumer Staples": [
        "WMT",  "PG",   "COST", "KO",   "PEP",
        "PM",   "MO",   "CL",   "MDLZ", "GIS",
        "SYY",  "KHC",
    ],
    "Materials": [
        "FCX",  "LIN",  "APD",  "ECL",  "NEM",
        "NUE",  "VMC",  "MLM",  "ALB",  "CF",
        "PPG",  "IFF",
    ],
    "Utilities": [
        "NEE",  "DUK",  "SO",   "D",    "AEP",
        "EXC",  "SRE",  "PEG",  "ED",   "XEL",
        "AWK",  "ES",
    ],
    "Real Estate": [
        "PLD",  "AMT",  "EQIX", "CCI",  "PSA",
        "O",    "WELL", "SPG",  "DLR",  "EQR",
        "VTR",  "ARE",
    ],
}

# ---------------------------------------------------------------------------
# Momentum helpers
# ---------------------------------------------------------------------------

def _compute_score(closes: List[float]) -> Optional[float]:
    """
    Compute the composite momentum score for a ticker given its list of daily
    closing prices (oldest → newest).

    Requires at least 62 bars (61 for mom_60d look-back + 1 current).
    Returns None if insufficient data.

    mom_5d  = (close[-1] - close[-6])  / close[-6]
    mom_20d = (close[-1] - close[-21]) / close[-21]
    mom_60d = (close[-1] - close[-61]) / close[-61]
    score   = 0.5*mom_5d + 0.3*mom_20d + 0.2*mom_60d
    """
    if len(closes) < 62:
        return None
    c = closes[-1]
    mom_5d  = (c - closes[-6])  / closes[-6]
    mom_20d = (c - closes[-21]) / closes[-21]
    mom_60d = (c - closes[-61]) / closes[-61]
    return 0.5 * mom_5d + 0.3 * mom_20d + 0.2 * mom_60d


def _compute_mom_10d(closes: List[float]) -> Optional[float]:
    """
    mom_10d = (close[-1] - close[-11]) / close[-11]
    Returns None if fewer than 12 bars.
    Used for the parabolic-reversal filter (exclude if > 15%).
    """
    if len(closes) < 12:
        return None
    return (closes[-1] - closes[-11]) / closes[-11]


# ---------------------------------------------------------------------------
# Date range helper
# ---------------------------------------------------------------------------

def _date_range(lookback_calendar_days: int = 120) -> tuple[str, str]:
    """Return (start, end) YYYY-MM-DD strings covering the required look-back."""
    end   = datetime.utcnow().date()
    start = end - timedelta(days=lookback_calendar_days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_top_movers_by_sector(n_per_sector: int = 2) -> Dict[str, List[str]]:
    """
    Returns {sector: [ticker1, ticker2, ...]} — the top n_per_sector momentum
    leaders per sector from SP500_UNIVERSE.

    Scoring:
        score = 0.5*mom_5d + 0.3*mom_20d + 0.2*mom_60d

    Reversal filter:
        Exclude any ticker where mom_10d > 15%  (parabolic / mean-reversion risk)

    Fallback:
        If ALL tickers in a sector fail (no usable data), fall back to the
        static SECTOR_MAP tickers from config.py and log the reason.

    Never raises.
    """
    start, end = _date_range(lookback_calendar_days=120)
    result: Dict[str, List[str]] = {}

    for sector, tickers in SP500_UNIVERSE.items():
        scored: List[tuple[float, str]] = []   # (score, ticker)

        for ticker in tickers:
            try:
                bars = get_ohlcv(ticker, start, end)
                if not bars:
                    continue
                closes = [b["close"] for b in bars]

                # Reversal filter — skip parabolic movers
                mom_10d = _compute_mom_10d(closes)
                if mom_10d is not None and mom_10d > 0.15:
                    continue

                score = _compute_score(closes)
                if score is None:
                    # Not enough history — skip silently
                    continue

                scored.append((score, ticker))

            except Exception as exc:
                # Individual ticker failure is non-fatal; just skip it
                _ = exc  # suppress unused-variable warning
                continue

        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            result[sector] = [t for _, t in scored[:n_per_sector]]
        else:
            # ALL tickers in this sector failed — fall back to SECTOR_MAP
            fallback_tickers = SECTOR_MAP.get(sector, [])
            reason = (
                f"no usable data for any ticker in sector '{sector}' "
                f"(date range {start} → {end})"
            )
            print(f"[universe] fallback activated: {reason}")
            result[sector] = fallback_tickers[:n_per_sector]

    return result


def format_universe_summary(movers: Dict[str, List[str]]) -> str:
    """
    Return a compact Telegram-friendly string listing top picks per sector.

    Example output:
        Top movers by sector:
        • Technology: NVDA, AAPL
        • Financials: JPM, GS
        ...
    """
    lines = ["Top movers by sector:"]
    for sector, tickers in movers.items():
        tickers_str = ", ".join(tickers) if tickers else "—"
        lines.append(f"• {sector}: {tickers_str}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Quick smoke-test when run directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Running universe scanner smoke-test (uses live market data) …")
    movers = get_top_movers_by_sector(n_per_sector=2)
    print(format_universe_summary(movers))
