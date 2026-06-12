"""
Penny Stock Unusual Volume Breakout Scanner
Runs ~9:30–10:30 AM ET (Mon–Fri) via GitHub Actions — earliest available slot.
Cron: 0 7 * * 1-5 UTC (3:00 AM ET) + ~3h–4h GHA queue delay.
Market-open guard waits until 9:30 AM ET if runner arrives early.

Strategy: Catch liquid sub-$10 stocks where volume is spiking 3× above normal
on a price breakout. Pure supply/demand mechanics — when unusual volume hits a
penny stock, it's either news-driven or early institutional activity.

Entry conditions (ALL must pass):
  1. Current price in [PENNY_MIN_PRICE, PENNY_MAX_PRICE] range
  2. 30-day avg daily volume ≥ PENNY_MIN_AVG_DAILY_VOL (liquidity floor)
  3. 3-day avg volume > PENNY_VOLUME_SPIKE_RATIO × 30-day avg (spike)
  4. Price > 20-day EMA (trend filter — avoids catching falling knives)
  5. Price making 10-day high (breakout confirmation)
  6. At least 1 Finnhub news item in the past 24 hours (catalyst filter)

LONG ONLY — no short signals. Short-selling penny stocks has extreme borrow
costs and squeeze risk. Long-only regardless of market regime.

Exit: TP +8%, SL -5%, auto-close after 2 trading days.
Capital: separate $5K penny_capital pool, max 5 concurrent at 20% each.

Usage:
  python penny_scan.py           # live mode
  python penny_scan.py --dry-run # prints signals, no writes, no Telegram
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    PENNY_UNIVERSE,
    PENNY_MIN_PRICE, PENNY_MAX_PRICE,
    PENNY_MIN_AVG_DAILY_VOL, PENNY_VOLUME_SPIKE_RATIO,
    PENNY_TARGET_PCT, PENNY_STOP_PCT, PENNY_HOLD_DAYS,
)
from tools.market_data import get_ohlcv, get_latest_price
from tools.session_manager import (
    get_portfolio, add_penny_signal, get_open_penny_signals,
)
from tools.telegram_bot import broadcast_message


# ---------------------------------------------------------------------------
# DST gate + market-open guard
# ---------------------------------------------------------------------------

def _within_dst_gate() -> bool:
    et_hour = datetime.now(ZoneInfo("America/New_York")).hour
    return 8 <= et_hour < 14   # 8:30 AM–2 PM ET (penny stocks need morning volume)


def _wait_for_market_open(max_wait_minutes: int = 30) -> None:
    """Wait until 9:30 AM ET if we arrive early — penny stocks need post-open volume."""
    et = ZoneInfo("America/New_York")
    now_et = datetime.now(et)
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    if now_et >= market_open:
        return
    wait_secs = (market_open - now_et).total_seconds()
    max_secs  = max_wait_minutes * 60
    sleep_for = min(wait_secs, max_secs)
    print(f"[penny] Pre-market ({now_et.strftime('%H:%M ET')}) — waiting {sleep_for/60:.1f} min for open")
    time.sleep(sleep_for)
    print("[penny] Market open — scanning now.")


# ---------------------------------------------------------------------------
# News catalyst check (Finnhub)
# ---------------------------------------------------------------------------

def _has_recent_news(ticker: str, hours: int = 24) -> bool:
    """Return True if Finnhub has at least 1 news item for ticker in the past `hours`."""
    try:
        import os as _os
        import requests
        api_key = _os.getenv("FINNHUB_API_KEY", "")
        if not api_key:
            return True  # can't check — pass through to avoid blocking all signals
        since  = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d")
        today  = datetime.utcnow().strftime("%Y-%m-%d")
        url    = f"https://finnhub.io/api/v1/company-news?symbol={ticker}&from={since}&to={today}&token={api_key}"
        resp   = requests.get(url, timeout=5)
        if resp.status_code == 200:
            articles = resp.json()
            return len(articles) >= 1
    except Exception:
        pass
    return True  # fail-open: don't block signals on API errors


# ---------------------------------------------------------------------------
# Scan logic
# ---------------------------------------------------------------------------

def _scan(today: str, dry_run: bool = False) -> list:
    """
    Scan PENNY_UNIVERSE for unusual volume breakout signals.
    Returns list of signal dicts that passed all filters.
    """
    open_signals  = get_open_penny_signals()
    open_tickers  = {s["ticker"] for s in open_signals}

    lookback_start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=45)).strftime("%Y-%m-%d")
    auto_close     = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=PENNY_HOLD_DAYS)).strftime("%Y-%m-%d")

    signals = []

    for ticker in PENNY_UNIVERSE:
        if ticker in open_tickers:
            print(f"[penny] {ticker} — skipped (already open)")
            continue

        try:
            bars = get_ohlcv(ticker, lookback_start, today)
            if len(bars) < 15:
                print(f"[penny] {ticker} — skipped (insufficient bars: {len(bars)})")
                continue

            closes  = [b["close"]  for b in bars]
            volumes = [b["volume"] for b in bars if b.get("volume", 0) > 0]

            current_price = closes[-1]

            # 1. Price range filter
            if not (PENNY_MIN_PRICE <= current_price <= PENNY_MAX_PRICE):
                print(f"[penny] {ticker} — price ${current_price:.2f} outside [${PENNY_MIN_PRICE}, ${PENNY_MAX_PRICE}]")
                continue

            if len(volumes) < 10:
                continue

            # 2. Liquidity floor: 30-day avg volume ≥ PENNY_MIN_AVG_DAILY_VOL
            avg_30d = sum(volumes[-30:]) / min(30, len(volumes))
            if avg_30d < PENNY_MIN_AVG_DAILY_VOL:
                print(f"[penny] {ticker} — avg vol {avg_30d:,.0f} < {PENNY_MIN_AVG_DAILY_VOL:,.0f} (illiquid)")
                continue

            # 3. Volume spike: 3-day avg > PENNY_VOLUME_SPIKE_RATIO × 30-day avg
            avg_3d    = sum(volumes[-3:]) / 3
            vol_ratio = (avg_3d / avg_30d) if avg_30d > 0 else 0
            if vol_ratio < PENNY_VOLUME_SPIKE_RATIO:
                print(f"[penny] {ticker} — volume spike {vol_ratio:.1f}x < {PENNY_VOLUME_SPIKE_RATIO}x")
                continue

            # 4. Price > 20-day EMA
            if len(closes) >= 20:
                ema20 = closes[-20]
                for c in closes[-19:]:
                    ema20 = ema20 * (1 - 2 / 21) + c * (2 / 21)
                if current_price <= ema20 * 0.999:
                    print(f"[penny] {ticker} — below 20d EMA (${current_price:.2f} vs ${ema20:.2f})")
                    continue
            else:
                ema20 = current_price  # insufficient history

            # 5. 10-day price high (breakout confirmation)
            high_10d = max(closes[-10:]) if len(closes) >= 10 else current_price
            if current_price < high_10d * 0.999:
                print(f"[penny] {ticker} — not at 10d high (${current_price:.2f} vs ${high_10d:.2f})")
                continue

            # 6. News catalyst required — no signal without a reason
            if not dry_run and not _has_recent_news(ticker, hours=24):
                print(f"[penny] {ticker} — no recent news (pump-and-dump filter)")
                continue

            entry_price  = round(get_latest_price(ticker), 4)
            if not (PENNY_MIN_PRICE <= entry_price <= PENNY_MAX_PRICE):
                print(f"[penny] {ticker} — live price ${entry_price:.4f} moved outside range")
                continue

            target_price = round(entry_price * (1 + PENNY_TARGET_PCT / 100), 4)
            stop_price   = round(entry_price * (1 - PENNY_STOP_PCT   / 100), 4)
            rrr          = round(PENNY_TARGET_PCT / PENNY_STOP_PCT, 2)

            signal = {
                "id":             f"PEN-{today}-{ticker}",
                "ticker":         ticker,
                "signal_type":    "penny_breakout",
                "direction":      "long",   # LONG ONLY — never short penny stocks
                "generated_date": today,
                "entry_price":    entry_price,
                "target_price":   target_price,
                "stop_price":     stop_price,
                "target_pct":     PENNY_TARGET_PCT,
                "stop_pct":       PENNY_STOP_PCT,
                "rrr":            rrr,
                "status":         "open",
                "exit_price":     None,
                "exit_date":      None,
                "pnl_pct":        None,
                "outcome":        None,
                "auto_close_date": auto_close,
                "rationale": (
                    f"vol spike {vol_ratio:.1f}x | price ${current_price:.4f} at 10d high ${high_10d:.4f} | "
                    f"above EMA20 ${ema20:.4f}"
                ),
            }
            signals.append(signal)
            print(f"[penny] SIGNAL: {ticker} @ ${entry_price:.4f}  vol={vol_ratio:.1f}x  "
                  f"TP=${target_price:.4f}  SL=${stop_price:.4f}")

        except Exception as e:
            print(f"[penny] {ticker} error: {e}")

    return signals


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print signals without writing or alerting")
    args = parser.parse_args()

    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    print(f"\n[penny] ========== Penny Stock Scan {today} ==========")

    if not _within_dst_gate():
        print("[penny] Outside operating window (8:30 AM–2 PM ET). Exiting.")
        return

    if not args.dry_run:
        _wait_for_market_open(max_wait_minutes=30)

    if not get_portfolio()["session"]["active"]:
        print("[penny] No active session. Exiting.")
        return

    signals = _scan(today, dry_run=args.dry_run)
    print(f"[penny] {len(signals)} signal(s) generated.")

    if args.dry_run:
        for s in signals:
            print(f"  DRY-RUN: {s['ticker']} entry=${s['entry_price']:.4f} "
                  f"tp=${s['target_price']:.4f} sl=${s['stop_price']:.4f}  {s['rationale']}")
        return

    pc = get_portfolio().get("penny_capital", {})
    print(f"[penny] Pool: ${pc.get('cash', 5000):.0f} cash / ${pc.get('equity', 5000):.0f} equity")

    for signal in signals:
        add_penny_signal(signal)

        msg = (
            f"🪙 <b>Penny Signal — {signal['ticker']}</b>\n\n"
            f"Entry:  <b>${signal['entry_price']:.4f}</b>\n"
            f"Target: ${signal['target_price']:.4f} (+{signal['target_pct']:.0f}%)\n"
            f"Stop:   ${signal['stop_price']:.4f} (-{signal['stop_pct']:.0f}%)\n"
            f"RRR:    {signal['rrr']:.1f}:1  |  Hold: {PENNY_HOLD_DAYS} days\n\n"
            f"<i>{signal['rationale']}</i>\n"
            f"<i>⚠️ Penny stocks are volatile. Position sized at 20% of pool max.</i>"
        )
        try:
            broadcast_message(msg)
        except Exception as e:
            print(f"[penny] Telegram error: {e}")

    if not signals:
        print("[penny] No signals today — no notification sent.")

    # Commit updated portfolio.json
    try:
        subprocess.run(["git", "add", "docs/portfolio.json"], check=True)
        subprocess.run(
            ["git", "commit", "-m", f"penny scan {today}: {len(signals)} signal(s)"],
            check=True,
        )
        subprocess.run(["git", "pull", "--rebase"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("[penny] portfolio.json committed and pushed.")
    except subprocess.CalledProcessError as e:
        print(f"[penny] Git error: {e}")


if __name__ == "__main__":
    main()
