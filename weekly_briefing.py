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
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import WATCHLIST, STOCKS, CRYPTO
from tools.market_data import get_ohlcv
from tools.market_regime import get_full_regime
from tools.sector_analysis import get_sector_strength, format_sector_heatmap
from tools.session_manager import get_portfolio, get_session_day, save_portfolio
from tools.telegram_bot import broadcast_message
from tools.agent_tracker import update_portfolio_tracker, format_ic_report


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
            _now  = datetime.now(ZoneInfo("America/New_York"))
            end   = _now.strftime("%Y-%m-%d")
            start = (_now - timedelta(days=10)).strftime("%Y-%m-%d")
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
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
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

    # Alpha vs SPY
    spy_return_pct  = round(portfolio.get("stats", {}).get("benchmark_return_pct", 0) or 0, 1)
    alpha_pct       = round(ret - spy_return_pct, 1)
    alpha_sign      = "+" if alpha_pct >= 0 else ""
    cash            = portfolio.get("cash", initial)
    deployed_pct    = round((1 - cash / equity) * 100, 1) if equity > 0 else 0.0

    # Mid-cap pool P&L
    midcap_cap    = portfolio.get("midcap_capital", {})
    mc_initial    = midcap_cap.get("initial", 5000.0)
    mc_equity     = midcap_cap.get("equity", mc_initial)
    mc_ret        = round((mc_equity - mc_initial) / mc_initial * 100, 2) if mc_initial else 0.0
    mc_signals    = len([s for s in portfolio.get("midcap_signals", []) if s.get("status") == "closed"])

    # Penny pool P&L
    penny_cap     = portfolio.get("penny_capital", {})
    pen_initial   = penny_cap.get("initial", 5000.0)
    pen_equity    = penny_cap.get("equity", pen_initial)
    pen_ret       = round((pen_equity - pen_initial) / pen_initial * 100, 2) if pen_initial else 0.0
    pen_signals   = len([s for s in portfolio.get("penny_signals", []) if s.get("status") == "closed"])

    week_num = max(1, round(session_day / 5))

    lines = [f"📅 <b>WEEK AHEAD BRIEFING</b>  |  Week of {today}\n"]

    # Session performance header
    if portfolio["session"]["active"]:
        ret_sign   = "+" if ret >= 0 else ""
        spy_sign   = "+" if spy_return_pct >= 0 else ""
        mc_sign  = "+" if mc_ret  >= 0 else ""
        pen_sign = "+" if pen_ret >= 0 else ""
        lines.append(
            f"📊 <b>Week {week_num} of {total_days // 5} — Session Performance</b>\n"
            f"  Portfolio:   {ret_sign}{ret:.1f}%  |  SPY: {spy_sign}{spy_return_pct:.1f}%  |  "
            f"Alpha: <b>{alpha_sign}{alpha_pct:.1f}%</b>\n"
            f"  Capital deployed: {deployed_pct:.0f}%  (target: 70%)\n"
            f"  Mid-cap pool: {mc_signals} closed signals  |  P&L: {mc_sign}{mc_ret:.2f}%  "
            f"(${mc_equity - mc_initial:+,.0f})\n"
            f"  Penny pool:   {pen_signals} closed signals  |  P&L: {pen_sign}{pen_ret:.2f}%  "
            f"(${pen_equity - pen_initial:+,.0f})\n"
        )

    # VIX
    v_emoji = _vix_emoji(vix)
    lines.append(
        f"{v_emoji} <b>VIX:</b> {f'{vix:.1f}' if vix is not None else 'N/A'}  —  {vix_label}\n"
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

    # Sector strength heatmap
    try:
        sector_strength = get_sector_strength()
        lines.append("")
        lines.append(format_sector_heatmap(sector_strength))
    except Exception as e:
        print(f"[weekly] Sector strength error: {e}")

    # Agent IC report — appended when data is available (Phase 3+)
    try:
        portfolio = update_portfolio_tracker(portfolio)
        save_portfolio(portfolio)
        ic_report = format_ic_report(portfolio)
        if ic_report:
            lines.append("\n" + ic_report)
    except Exception as e:
        print(f"[weekly] IC report error: {e}")

    broadcast_message("\n".join(lines))
    print("[weekly] Briefing sent.")


if __name__ == "__main__":
    main()
