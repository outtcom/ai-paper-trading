"""
Weekly intelligence briefing — runs every Sunday at 6:00 PM ET.
Triggered by GitHub Actions.

Sends a Telegram message covering:
  - VIX level and implied sizing for the week
  - SPY + QQQ trend (above/below 50/200-day MA)
  - Bitcoin weekend performance (crypto trades 24/7)
  - Earnings calendar for all watchlist stocks this week
  - Tactical watchlist notes per ticker

This is the Sunday evening brief — read it before Monday open.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import WATCHLIST, STOCKS, CRYPTO
from tools.market_data import get_latest_price, get_ohlcv
from tools.market_regime import get_full_regime
from tools.session_manager import get_portfolio, get_session_day
from tools.telegram_bot import send_message

from datetime import timedelta


def _trend_emoji(above_ma50, above_ma200):
    if above_ma50 and above_ma200:   return "📈 BULLISH"
    if not above_ma50 and not above_ma200: return "📉 BEARISH"
    return "↔️ MIXED"


def _vix_emoji(vix):
    if vix is None:   return "❓"
    if vix < 18:      return "🟢"
    if vix < 25:      return "🟡"
    if vix < 35:      return "🔴"
    return "🚨"


def _stock_watchlist_notes(stocks, earnings_map) -> list:
    """Quick one-liner per stock: earnings flag + 5-day momentum."""
    lines = []
    for ticker in stocks:
        try:
            end   = datetime.today().strftime("%Y-%m-%d")
            start = (datetime.today() - timedelta(days=10)).strftime("%Y-%m-%d")
            bars  = get_ohlcv(ticker, start, end)
            if len(bars) >= 5:
                pct_5d = round((bars[-1]["close"] - bars[-5]["close"]) / bars[-5]["close"] * 100, 2)
                mom = f"{'+' if pct_5d >= 0 else ''}{pct_5d:.1f}% (5d)"
            else:
                mom = "N/A"
        except Exception:
            mom = "N/A"

        e = earnings_map.get(ticker, {})
        earnings_flag = f" ⚠️ EARNINGS {e['date']}" if e.get("has_earnings") else ""
        lines.append(f"  {ticker}: {mom}{earnings_flag}")
    return lines


def _crypto_notes(crypto_perf) -> list:
    lines = []
    for ticker, data in crypto_perf.items():
        if not data:
            lines.append(f"  {ticker}: data unavailable")
            continue
        price = data.get("price", 0)
        pct   = data.get("pct_1d", 0)
        sign  = "+" if pct >= 0 else ""
        emoji = "📈" if pct >= 0 else "📉"
        name  = ticker.replace("-USD", "")
        lines.append(f"  {emoji} {name}: ${price:,.0f}  ({sign}{pct:.1f}% today)")
    return lines


def main():
    today = datetime.today().strftime("%Y-%m-%d")
    print(f"\n[weekly] ========== Weekly Briefing {today} ==========")

    regime   = get_full_regime(WATCHLIST)
    portfolio = get_portfolio()
    session_day = get_session_day()
    total_days  = portfolio["session"]["total_days"]
    equity      = portfolio.get("equity", portfolio["initial_capital"])
    initial     = portfolio["initial_capital"]
    ret         = round((equity - initial) / initial * 100, 2)

    vix      = regime.get("vix")
    vix_label = regime.get("vix_label", "Unknown")
    vix_mult  = regime.get("vix_multiplier", 1.0)
    spy       = regime.get("spy", {})
    qqq       = regime.get("qqq", {})
    btc_trend = regime.get("btc", {})
    crypto_perf = regime.get("crypto_perf", {})
    earnings  = regime.get("earnings", {})

    lines = [f"📅 <b>WEEK AHEAD BRIEFING</b>  |  Week of {today}\n"]

    # Session status
    if portfolio["session"]["active"]:
        sign = "+" if ret >= 0 else ""
        lines.append(
            f"💼 <b>Session:</b> Day {session_day}/{total_days}  |  "
            f"Equity ${equity:,.2f} ({sign}{ret:.1f}%)\n"
        )

    # VIX
    v_emoji = _vix_emoji(vix)
    lines.append(
        f"{v_emoji} <b>VIX:</b> {vix:.1f if vix else 'N/A'}  —  {vix_label}\n"
        f"   Position sizing this week: <b>{int(vix_mult * 100)}%</b> of normal\n"
    )

    # Market trend
    spy_label = _trend_emoji(spy.get("above_ma50"), spy.get("above_ma200"))
    qqq_label = _trend_emoji(qqq.get("above_ma50"), qqq.get("above_ma200"))
    lines.append(
        f"📊 <b>Market Regime:</b>\n"
        f"  SPY ${spy.get('price', '?')}  {spy_label}  "
        f"({'+' if spy.get('pct_vs_ma50', 0) >= 0 else ''}{spy.get('pct_vs_ma50', 0):.1f}% vs MA50)\n"
        f"  QQQ ${qqq.get('price', '?')}  {qqq_label}  "
        f"({'+' if qqq.get('pct_vs_ma50', 0) >= 0 else ''}{qqq.get('pct_vs_ma50', 0):.1f}% vs MA50)\n"
    )

    # Crypto weekend recap
    crypto_lines = _crypto_notes(crypto_perf)
    if crypto_lines:
        btc_t = _trend_emoji(btc_trend.get("above_ma50"), btc_trend.get("above_ma200"))
        lines.append(
            f"₿ <b>Crypto (weekend):</b>  BTC trend: {btc_t}\n" +
            "\n".join(crypto_lines) + "\n"
        )

    # Earnings this week
    upcoming = {t: e for t, e in earnings.items() if e.get("has_earnings")}
    if upcoming:
        lines.append("⚠️ <b>EARNINGS THIS WEEK — DO NOT hold into these:</b>")
        for t, e in sorted(upcoming.items(), key=lambda x: x[1].get("days_until", 99)):
            lines.append(f"  ❌ {t} — {e['date']} ({e['days_until']}d)")
        lines.append("")

    # Watchlist notes
    lines.append("🎯 <b>Watchlist (5-day momentum):</b>")
    lines += _stock_watchlist_notes(STOCKS, earnings)
    lines.append("")

    # Key reminders
    lines.append(
        "📌 <b>Reminders:</b>\n"
        "  • No trades on FOMC day (check calendar)\n"
        "  • Avoid first 30 min after open (9:30–10:00 AM)\n"
        f"  • Max position this week: {int(vix_mult * 25)}% of $5k "
        f"(VIX-adjusted from 25%)"
    )

    send_message("\n".join(lines))
    print("[weekly] Briefing sent.")


if __name__ == "__main__":
    main()
