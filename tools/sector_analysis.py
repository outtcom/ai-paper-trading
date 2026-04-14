"""
Sector strength and momentum analysis tool.

Uses SPDR sector ETFs (XLK, XLC, XLY, XLV, XLF, XLE, XLI, XLP, XLB, XLU, XLRE)
as proxies for each GICS sector.

Key functions:
  get_sector_strength()          — rank all sectors by composite momentum score
  get_ticker_sector_rank(ticker) — return this ticker's sector rank (1=strongest)
  get_sector_bonus(ticker)       — 0.0–0.5 score bonus based on sector strength
  format_sector_heatmap()        — Telegram-ready sector strength summary
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from tools.market_data import get_ohlcv, get_latest_price
from config import SECTOR_ETFS, TICKER_SECTOR


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def _sector_momentum(etf: str) -> dict:
    """
    Compute momentum metrics for a single sector ETF.
    Returns a dict with: price, mom_1m, mom_5d, mom_3m, above_ma50, rsi_14, vs_spy
    """
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=100)).strftime("%Y-%m-%d")

    try:
        bars = get_ohlcv(etf, start, end)
    except Exception as e:
        return {"etf": etf, "error": str(e), "score": 0.0}

    if len(bars) < 10:
        return {"etf": etf, "error": "insufficient data", "score": 0.0}

    closes = [b["close"] for b in bars]
    current = closes[-1]

    # Momentum periods
    mom_5d  = round((current - closes[-6])  / closes[-6]  * 100, 2) if len(closes) >= 6  else 0
    mom_1m  = round((current - closes[-22]) / closes[-22] * 100, 2) if len(closes) >= 22 else 0
    mom_3m  = round((current - closes[-63]) / closes[-63] * 100, 2) if len(closes) >= 63 else 0

    # MA50
    ma50 = sum(closes[-50:]) / min(50, len(closes))
    above_ma50 = current > ma50

    # RSI-14
    rsi = _rsi(closes[-15:]) if len(closes) >= 15 else 50.0

    # Composite score: weight 1m most, then 5d, then 3m
    score = (mom_1m * 0.5) + (mom_5d * 0.3) + (mom_3m * 0.2)
    # Bonus for being above MA50 (trend confirmation)
    if above_ma50:
        score += 1.0

    return {
        "etf":       etf,
        "price":     round(current, 2),
        "mom_5d":    mom_5d,
        "mom_1m":    mom_1m,
        "mom_3m":    mom_3m,
        "above_ma50": above_ma50,
        "rsi_14":    round(rsi, 1),
        "score":     round(score, 2),
    }


def _rsi(closes: list, period: int = 14) -> float:
    """Calculate RSI from a list of closing prices."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_sector_strength() -> dict:
    """
    Rank all 11 GICS sectors by composite momentum score.

    Returns:
    {
        "sectors": {
            "Technology":   {"etf": "XLK", "score": 4.2, "mom_1m": 3.1, ...},
            ...
        },
        "ranking":  ["Technology", "Financials", ...],   # best → worst
        "top_3":    ["Technology", "Financials", "Healthcare"],
        "bottom_3": ["Energy", "Utilities", "Real Estate"],
        "as_of":    "2026-04-14",
    }
    """
    sector_data = {}
    for sector, etf in SECTOR_ETFS.items():
        sector_data[sector] = _sector_momentum(etf)
        sector_data[sector]["sector"] = sector

    # Sort by composite score descending
    ranking = sorted(
        sector_data.keys(),
        key=lambda s: sector_data[s].get("score", 0),
        reverse=True,
    )

    return {
        "sectors":  sector_data,
        "ranking":  ranking,
        "top_3":    ranking[:3],
        "bottom_3": ranking[-3:],
        "as_of":    datetime.today().strftime("%Y-%m-%d"),
    }


def get_ticker_sector(ticker: str) -> str:
    """Return the GICS sector name for a ticker."""
    return TICKER_SECTOR.get(ticker, "Unknown")


def get_ticker_sector_rank(ticker: str, sector_strength: dict) -> int:
    """
    Return this ticker's sector rank (1 = strongest sector, 11 = weakest).
    Returns 6 (neutral) if sector not found.
    """
    sector = get_ticker_sector(ticker)
    ranking = sector_strength.get("ranking", [])
    if sector in ranking:
        return ranking.index(sector) + 1
    return 6  # neutral default


def get_sector_bonus(ticker: str, sector_strength: dict) -> float:
    """
    Return a conviction bonus (0.0 – 0.5) based on sector rank.
    Top-3 sector → +0.5, Top-5 → +0.25, Bottom-3 → -0.25.
    Used to break ties between BUY candidates.
    """
    rank = get_ticker_sector_rank(ticker, sector_strength)
    n = len(sector_strength.get("ranking", [11]))
    if rank <= 2:           return 0.5
    elif rank <= 4:         return 0.25
    elif rank >= n - 1:     return -0.25   # bottom 2 sectors
    return 0.0


def format_sector_heatmap(sector_strength: dict) -> str:
    """
    Format a Telegram-ready sector strength heatmap.
    """
    ranking = sector_strength.get("ranking", [])
    sectors = sector_strength.get("sectors", {})
    n = len(ranking)

    lines = [f"📊 <b>SECTOR STRENGTH — {sector_strength.get('as_of', 'today')}</b>\n"]

    def _bar(rank, total):
        filled = total - rank + 1
        return "█" * filled + "░" * (rank - 1)

    for i, sector in enumerate(ranking):
        data = sectors.get(sector, {})
        score = data.get("score", 0)
        mom_1m = data.get("mom_1m", 0)
        mom_5d = data.get("mom_5d", 0)
        etf = data.get("etf", "")
        above = "↑" if data.get("above_ma50") else "↓"
        sign_1m = "+" if mom_1m >= 0 else ""
        sign_5d = "+" if mom_5d >= 0 else ""

        # Emoji based on rank
        if i < 3:       medal = "🟢"
        elif i < 5:     medal = "🟡"
        elif i < 8:     medal = "🟠"
        else:           medal = "🔴"

        lines.append(
            f"{medal} <b>{sector}</b> ({etf}) {above}\n"
            f"   1m: {sign_1m}{mom_1m:.1f}%  5d: {sign_5d}{mom_5d:.1f}%  Score: {score:.1f}"
        )

    lines.append(
        f"\n🟢 Buy in: {', '.join(sector_strength.get('top_3', []))}\n"
        f"🔴 Avoid:  {', '.join(sector_strength.get('bottom_3', []))}"
    )

    return "\n".join(lines)
