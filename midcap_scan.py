"""
Mid-Cap Relative Strength Breakout Scanner
Runs ~10:30 AM ET (Mon–Fri) via GitHub Actions.
Cron: 10 11 * * 1-5 UTC (7:10 AM ET) + ~3h20min GHA queue delay.

Strategy: Catch mid-cap stocks ($2B–$10B mkt cap) outperforming their sector
ETF while making new 20-day price highs. The institutional accumulation sweet
spot — liquid enough to trade, small enough to move on inflows.

Entry conditions (ALL must pass):
  1. Price making a 20-day high (momentum confirmation)
  2. Stock's 5-day return > sector ETF 5-day return (outperforming sector)
  3. 3-day avg volume ≥ MIDCAP_MIN_VOLUME_RATIO × 20-day avg volume
  4. Price > 20-day EMA (trend filter)
  5. RSI-14 between MIDCAP_MIN_RSI and MIDCAP_MAX_RSI

Exit: TP +5%, SL -3%, auto-close after 5 trading days.
Capital: separate $5K midcap_capital pool, max 4 concurrent signals.
Sector filter: reads today's strategy_brief.json and skips avoid_sectors.

Usage:
  python midcap_scan.py           # live mode — generates signals, commits portfolio.json
  python midcap_scan.py --dry-run # prints signals, no writes, no Telegram
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    MIDCAP_UNIVERSE, SECTOR_ETFS, TICKER_SECTOR,
    MIDCAP_TARGET_PCT, MIDCAP_STOP_PCT, MIDCAP_HOLD_DAYS,
    MIDCAP_MIN_VOLUME_RATIO, MIDCAP_MIN_RSI, MIDCAP_MAX_RSI,
    STATE_DIR,
)
from tools.market_data import get_ohlcv, get_latest_price
from tools.session_manager import (
    get_portfolio, add_midcap_signal, get_open_midcap_signals,
)
from tools.telegram_bot import broadcast_message


# ---------------------------------------------------------------------------
# DST gate — skip if outside the expected ET arrival window
# ---------------------------------------------------------------------------

def _within_dst_gate() -> bool:
    et_hour = datetime.now(ZoneInfo("America/New_York")).hour
    return 8 <= et_hour < 15   # 8 AM–3 PM ET


# ---------------------------------------------------------------------------
# RSI-14 (Wilder's smoothed — standard 14-period)
# ---------------------------------------------------------------------------

def _rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    # Initial SMA seed
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # Wilder smoothing for remaining bars
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


# ---------------------------------------------------------------------------
# Sector ETF return helper
# ---------------------------------------------------------------------------

def _sector_etf_return_5d(sector: str, end_date: str) -> float:
    """Return the sector ETF's 5-day price return. Returns 0 on failure."""
    etf = SECTOR_ETFS.get(sector)
    if not etf:
        return 0.0
    try:
        start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=14)).strftime("%Y-%m-%d")
        bars  = get_ohlcv(etf, start, end_date)
        if len(bars) < 5:
            return 0.0
        close_5d_ago = bars[-5]["close"]
        close_now    = bars[-1]["close"]
        return round((close_now - close_5d_ago) / close_5d_ago * 100, 3) if close_5d_ago > 0 else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Load today's strategy brief to honour avoid_sectors
# ---------------------------------------------------------------------------

def _load_avoid_sectors(today: str) -> set:
    try:
        brief_path = os.path.join(STATE_DIR, today, "strategy_brief.json")
        with open(brief_path, "r", encoding="utf-8") as f:
            brief = json.load(f)
        return set(brief.get("avoid_sectors", []))
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# Main scan logic
# ---------------------------------------------------------------------------

def _scan(today: str, dry_run: bool = False) -> list:
    """
    Scan MIDCAP_UNIVERSE for relative strength breakout signals.
    Returns list of signal dicts that passed all filters.
    """
    avoid_sectors  = _load_avoid_sectors(today)
    open_signals   = get_open_midcap_signals()
    open_tickers   = {s["ticker"] for s in open_signals}

    lookback_start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=70)).strftime("%Y-%m-%d")
    auto_close     = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=MIDCAP_HOLD_DAYS)).strftime("%Y-%m-%d")

    signals = []

    for ticker in MIDCAP_UNIVERSE:
        if ticker in open_tickers:
            print(f"[midcap] {ticker} — skipped (already open)")
            continue

        sector = TICKER_SECTOR.get(ticker, "")
        if sector and sector in avoid_sectors:
            print(f"[midcap] {ticker} — skipped (avoid sector: {sector})")
            continue

        try:
            bars = get_ohlcv(ticker, lookback_start, today)
            if len(bars) < 25:
                print(f"[midcap] {ticker} — skipped (insufficient bars: {len(bars)})")
                continue

            closes  = [b["close"]  for b in bars]
            volumes = [b["volume"] for b in bars if b.get("volume", 0) > 0]

            current_price = closes[-1]
            if current_price <= 0:
                continue

            # 1. 20-day price high
            high_20d = max(closes[-20:])
            if current_price < high_20d * 0.999:   # 0.1% tolerance for float imprecision
                print(f"[midcap] {ticker} — not at 20d high (${current_price:.2f} vs ${high_20d:.2f})")
                continue

            # 2. Sector relative strength: stock 5d return > sector ETF 5d return
            if len(closes) >= 5:
                stock_5d = round((closes[-1] - closes[-5]) / closes[-5] * 100, 3) if closes[-5] > 0 else 0
            else:
                stock_5d = 0
            etf_5d = _sector_etf_return_5d(sector, today)
            if stock_5d <= etf_5d:
                print(f"[midcap] {ticker} — sector underperformance (stock {stock_5d:+.2f}% vs ETF {etf_5d:+.2f}%)")
                continue

            # 3. Volume confirmation: 3d avg ≥ MIDCAP_MIN_VOLUME_RATIO × 20d avg
            if len(volumes) >= 20:
                avg_20d = sum(volumes[-20:]) / min(20, len(volumes))
                avg_3d  = sum(volumes[-3:])  / 3
                vol_ratio = (avg_3d / avg_20d) if avg_20d > 0 else 0
                if vol_ratio < MIDCAP_MIN_VOLUME_RATIO:
                    print(f"[midcap] {ticker} — volume {vol_ratio:.2f}x < {MIDCAP_MIN_VOLUME_RATIO}x")
                    continue
            else:
                vol_ratio = 1.0  # insufficient history — pass

            # 4. Price > 20-day EMA
            ema20 = closes[-20]
            for c in closes[-19:]:
                ema20 = ema20 * (1 - 2 / 21) + c * (2 / 21)
            if current_price <= ema20 * 0.999:
                print(f"[midcap] {ticker} — below 20d EMA (${current_price:.2f} vs ${ema20:.2f})")
                continue

            # 5. RSI-14 in momentum range
            rsi = _rsi(closes)
            if not (MIDCAP_MIN_RSI <= rsi <= MIDCAP_MAX_RSI):
                print(f"[midcap] {ticker} — RSI {rsi:.1f} outside [{MIDCAP_MIN_RSI}, {MIDCAP_MAX_RSI}]")
                continue

            entry_price  = round(get_latest_price(ticker), 2)
            target_price = round(entry_price * (1 + MIDCAP_TARGET_PCT / 100), 2)
            stop_price   = round(entry_price * (1 - MIDCAP_STOP_PCT   / 100), 2)
            rrr          = round(MIDCAP_TARGET_PCT / MIDCAP_STOP_PCT, 2)

            signal = {
                "id":             f"MC-{today}-{ticker}",
                "ticker":         ticker,
                "signal_type":    "midcap_breakout",
                "sector":         sector,
                "direction":      "long",
                "generated_date": today,
                "entry_price":    entry_price,
                "target_price":   target_price,
                "stop_price":     stop_price,
                "target_pct":     MIDCAP_TARGET_PCT,
                "stop_pct":       MIDCAP_STOP_PCT,
                "rrr":            rrr,
                "status":         "open",
                "exit_price":     None,
                "exit_date":      None,
                "pnl_pct":        None,
                "outcome":        None,
                "auto_close_date": auto_close,
                "rationale": (
                    f"20d high ${high_20d:.2f} | stock +{stock_5d:.1f}% vs ETF +{etf_5d:.1f}% (5d) | "
                    f"vol {vol_ratio:.1f}x | RSI {rsi:.0f}"
                ),
            }
            signals.append(signal)
            print(f"[midcap] SIGNAL: {ticker} @ ${entry_price:.2f}  RSI={rsi:.0f}  "
                  f"rel_str={stock_5d - etf_5d:+.1f}%  vol={vol_ratio:.1f}x")

        except Exception as e:
            print(f"[midcap] {ticker} error: {e}")

    return signals


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print signals without writing or alerting")
    parser.add_argument("--force", action="store_true", help="Skip time-window gate (for manual/demo runs)")
    args = parser.parse_args()

    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    print(f"\n[midcap] ========== Mid-Cap Scan {today} ==========")

    if not args.force and not _within_dst_gate():
        print("[midcap] Outside operating window (8 AM–3 PM ET). Exiting.")
        return

    if not get_portfolio()["session"]["active"]:
        print("[midcap] No active session. Exiting.")
        return

    signals = _scan(today, dry_run=args.dry_run)
    print(f"[midcap] {len(signals)} signal(s) generated.")

    if args.dry_run:
        for s in signals:
            print(f"  DRY-RUN: {s['ticker']} entry=${s['entry_price']} "
                  f"tp=${s['target_price']} sl=${s['stop_price']}  {s['rationale']}")
        return

    mc = get_portfolio().get("midcap_capital", {})
    print(f"[midcap] Pool: ${mc.get('cash', 5000):.0f} cash / ${mc.get('equity', 5000):.0f} equity")

    for signal in signals:
        add_midcap_signal(signal)

        # Telegram notification
        msg = (
            f"📊 <b>Mid-Cap Signal — {signal['ticker']}</b>\n\n"
            f"Sector: {signal['sector']}\n"
            f"Entry:  <b>${signal['entry_price']:.2f}</b>\n"
            f"Target: ${signal['target_price']:.2f} (+{signal['target_pct']:.0f}%)\n"
            f"Stop:   ${signal['stop_price']:.2f} (-{signal['stop_pct']:.0f}%)\n"
            f"RRR:    {signal['rrr']:.1f}:1  |  Hold: {MIDCAP_HOLD_DAYS} days\n\n"
            f"<i>{signal['rationale']}</i>"
        )
        try:
            broadcast_message(msg)
        except Exception as e:
            print(f"[midcap] Telegram error: {e}")

    if not signals:
        print("[midcap] No signals today — no notification sent.")

    # Commit updated portfolio.json
    try:
        subprocess.run(["git", "add", "docs/portfolio.json"], check=True)
        subprocess.run(
            ["git", "commit", "-m", f"midcap scan {today}: {len(signals)} signal(s)"],
            check=True,
        )
        subprocess.run(["git", "pull", "--rebase"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("[midcap] portfolio.json committed and pushed.")
    except subprocess.CalledProcessError as e:
        print(f"[midcap] Git error: {e}")


if __name__ == "__main__":
    main()
