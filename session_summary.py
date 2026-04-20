"""
22-day session wrap-up. Called by eod_session.py on the final day, or manually.
Compares swing trade performance vs SPY benchmark AND day trade signal hypothetical P&L.
"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.market_data import _yahoo_direct_ohlcv
from tools.session_manager import get_portfolio
from tools.telegram_bot import broadcast_message


def calculate_swing_stats(portfolio: dict) -> dict:
    history = portfolio.get("trade_history", [])
    if not history:
        return {"count": 0, "win_rate": 0, "avg_pnl": 0, "total_return_pct": 0,
                "best": None, "worst": None}
    wins   = [t for t in history if t.get("pnl", 0) > 0]
    pnls   = [t.get("pnl_pct", 0) for t in history]
    best   = max(history, key=lambda t: t.get("pnl_pct", 0))
    worst  = min(history, key=lambda t: t.get("pnl_pct", 0))
    initial = portfolio["initial_capital"]
    equity  = portfolio.get("equity", initial)
    return {
        "count":            len(history),
        "win_rate":         round(len(wins) / len(history) * 100, 1),
        "avg_pnl":          round(sum(pnls) / len(pnls), 2),
        "total_return_pct": round((equity - initial) / initial * 100, 2),
        "best":             best,
        "worst":            worst,
    }


def calculate_spy_return(start_date: str, end_date: str) -> float:
    try:
        bars = _yahoo_direct_ohlcv("SPY", start_date, end_date)
        if len(bars) < 2:
            return None
        return round((bars[-1]["close"] - bars[0]["close"]) / bars[0]["close"] * 100, 2)
    except Exception:
        return None


def calculate_day_trade_stats(portfolio: dict) -> dict:
    signals = portfolio.get("day_trade_signals", [])
    closed  = [s for s in signals if s.get("status") == "closed"]
    if not closed:
        return {"count": 0, "win_rate": 0, "avg_pnl": 0, "hypothetical_total": 0}
    wins   = [s for s in closed if s.get("outcome") == "win"]
    pnls   = [s.get("pnl_pct") or 0 for s in closed]
    return {
        "count":              len(closed),
        "win_rate":           round(len(wins) / len(closed) * 100, 1),
        "avg_pnl":            round(sum(pnls) / len(pnls), 2),
        "hypothetical_total": round(sum(pnls), 2),
    }


def format_summary(swing: dict, spy_return, dt_stats: dict, session: dict) -> str:
    start = session.get("start_date", "?")
    end   = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    total = swing["total_return_pct"]
    sign  = "+" if total >= 0 else ""

    lines = [
        f"🏁 <b>{session.get('total_days', 22)}-Day Session Complete — Performance Review</b>",
        f"Period: {start} → {end}\n",
        "📈 <b>Swing Trades (Real Capital)</b>",
        f"Trades: {swing['count']}  |  Win Rate: {swing['win_rate']:.0f}%",
        f"Avg P&amp;L: {'+' if swing['avg_pnl'] >= 0 else ''}{swing['avg_pnl']:.2f}%  "
        f"|  Total Return: <b>{sign}{total:.2f}%</b>",
    ]
    if swing["best"]:
        b, w = swing["best"], swing["worst"]
        lines.append(
            f"Best: {b['ticker']} {'+' if b.get('pnl_pct', 0) >= 0 else ''}{b.get('pnl_pct', 0):.1f}%  "
            f"|  Worst: {w['ticker']} {w.get('pnl_pct', 0):.1f}%"
        )

    lines.append("")
    if spy_return is not None:
        alpha = round(total - spy_return, 2)
        beat  = "Beat SPY ✅" if alpha > 0 else "Underperformed ❌"
        lines += [
            "📊 <b>vs SPY Benchmark</b>",
            f"SPY: {'+' if spy_return >= 0 else ''}{spy_return:.2f}%   "
            f"Our return: {sign}{total:.2f}%   Alpha: {'+' if alpha >= 0 else ''}{alpha:.2f}%",
            f"[{beat}]",
        ]
    else:
        lines += ["📊 <b>vs SPY Benchmark</b>", "SPY return unavailable."]

    dt = dt_stats
    rec = ""
    if dt["count"] >= 5:
        rec = "→ Recommend: allocate small capital in session 2" if dt["win_rate"] >= 60 else "→ Needs more data / refine filters"
    lines += [
        "",
        "📡 <b>Day Trade Signals (Paper / Hypothetical)</b>",
        f"Signals: {dt['count']}  |  Win Rate: {dt['win_rate']:.0f}%",
        f"Avg P&amp;L: {'+' if dt['avg_pnl'] >= 0 else ''}{dt['avg_pnl']:.2f}%  "
        f"|  Hypothetical Total: {'+' if dt['hypothetical_total'] >= 0 else ''}{dt['hypothetical_total']:.2f}%",
    ]
    if rec:
        lines.append(rec)

    return "\n".join(lines)


def run():
    portfolio   = get_portfolio()
    session     = portfolio.get("session", {})
    start_date  = session.get("start_date", "2026-01-01")
    end_date    = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

    swing       = calculate_swing_stats(portfolio)
    spy_return  = calculate_spy_return(start_date, end_date)
    dt_stats    = calculate_day_trade_stats(portfolio)
    msg         = format_summary(swing, spy_return, dt_stats, session)
    broadcast_message(msg)
    print("[session_summary] Summary broadcast complete.")


if __name__ == "__main__":
    run()
