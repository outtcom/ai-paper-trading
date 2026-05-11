"""
ORB Scalping Scanner — runs at 10:00 AM ET (30 min after market open).
Triggered by GitHub Actions daily (Mon–Fri).

Scans all watchlist tickers for Opening Range Breakouts:
  - Computes the 9:30–10:00 AM range from 5-min bars
  - Signals when price breaks above range high (LONG) or below range low (SHORT)
  - Volume must be ≥ 1.3x the 5-min baseline (daily avg ÷ 78 bars)
  - Signals use a separate $5,000 scalping_capital pool
  - Auto-close at 12:00 PM ET via midday_check.py
"""
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    WATCHLIST,
    SCALPING_RANGE_MINUTES, SCALPING_TARGET_PCT, SCALPING_STOP_PCT,
    SCALPING_VOL_RATIO_MIN,
)
from tools.market_data import _yahoo_intraday_ohlcv, _yahoo_direct_ohlcv, get_latest_price
from tools.market_regime import is_event_blocked
from tools.session_manager import get_portfolio, add_scalping_signal
from tools.telegram_bot import broadcast_message, send_group_trade_signal


def _detect_orb_signals(watchlist: list, today: str) -> list:
    """
    Scan watchlist for Opening Range Breakout signals.
    Returns list of signal dicts added to portfolio.
    """
    signals = []
    n_range_bars = SCALPING_RANGE_MINUTES // 5   # 6 bars at 5-min resolution

    for ticker in watchlist:
        if ticker.endswith("-USD"):
            continue  # no crypto scalping

        # 5-min bars for today (regular session only)
        bars = _yahoo_intraday_ohlcv(ticker, today, "5m")
        if len(bars) < n_range_bars + 1:
            print(f"[scalping] {ticker}: only {len(bars)} bars — insufficient, skipping")
            continue

        # Opening range = first 30 min (bars[0..5])
        range_bars  = bars[:n_range_bars]
        range_high  = max(b["high"] for b in range_bars)
        range_low   = min(b["low"]  for b in range_bars)

        # Breakout bar = the first bar AFTER the opening range (bars[n_range_bars]).
        # Using bars[-1] caused stale signals because GHA runs 60-90 min late —
        # by then price has already reverted inside the range.
        last_bar = bars[n_range_bars]
        current  = last_bar["close"]
        cur_vol  = last_bar["volume"]

        # 30-day baseline: daily avg volume ÷ 78 five-min bars per session
        try:
            start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=35)).strftime("%Y-%m-%d")
            daily_bars = _yahoo_direct_ohlcv(ticker, start, today)
            recent = [b["volume"] for b in daily_bars[-30:] if b["volume"] > 0]
            avg_daily_vol    = sum(recent) / max(1, len(recent))
            baseline_5m_vol  = avg_daily_vol / 78
        except Exception:
            baseline_5m_vol = 0

        vol_ratio = (cur_vol / baseline_5m_vol) if baseline_5m_vol > 0 else 1.0

        # Require close to be at least 0.15% beyond the range boundary to avoid
        # false breakouts from brief pokes above/below the range high/low
        MIN_BREAKOUT_MARGIN = 0.0015
        broke_up   = (current > range_high * (1 + MIN_BREAKOUT_MARGIN)) and vol_ratio >= SCALPING_VOL_RATIO_MIN
        broke_down = (current < range_low  * (1 - MIN_BREAKOUT_MARGIN)) and vol_ratio >= SCALPING_VOL_RATIO_MIN

        if not broke_up and not broke_down:
            dir_label = "LONG" if current > range_high else ("SHORT" if current < range_low else "FLAT")
            print(f"[scalping] {ticker} @ ${current:.2f} — no breakout "
                  f"(range ${range_low:.2f}–${range_high:.2f}, vol {vol_ratio:.1f}x)")
            continue

        direction = "long" if broke_up else "short"
        if direction == "long":
            target = round(current * (1 + SCALPING_TARGET_PCT / 100), 2)
            stop   = round(current * (1 - SCALPING_STOP_PCT   / 100), 2)
        else:
            target = round(current * (1 - SCALPING_TARGET_PCT / 100), 2)
            stop   = round(current * (1 + SCALPING_STOP_PCT   / 100), 2)

        signal = {
            "id":               f"DTS-{today}-{ticker}-scalp",
            "ticker":           ticker,
            "signal_type":      "scalping_orb",
            "direction":        direction,
            "generated_date":   today,
            "entry_price":      round(current, 2),
            "target_price":     target,
            "target_pct":       SCALPING_TARGET_PCT,
            "stop_price":       stop,
            "stop_pct":         SCALPING_STOP_PCT,
            "range_high":       round(range_high, 2),
            "range_low":        round(range_low,  2),
            "status":           "open",
            "exit_price":       None,
            "exit_date":        None,
            "pnl_pct":          None,
            "pnl_usd":          None,
            "outcome":          None,
            "auto_close_date":  today,
            "rationale": (
                f"ORB {'breakout above' if direction == 'long' else 'breakdown below'} "
                f"${range_high if direction == 'long' else range_low:.2f} "
                f"(vol {vol_ratio:.1f}x)"
            ),
        }

        add_scalping_signal(signal)
        send_group_trade_signal(signal)
        signals.append(signal)
        print(f"[scalping] Signal: {ticker} ORB {direction.upper()} @ ${current:.2f}  "
              f"TP=${target:.2f}  SL=${stop:.2f}  (vol {vol_ratio:.1f}x)")

    return signals


def main():
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    print(f"\n[scalping] ========== ORB Scalping Scan {today} ==========")

    portfolio = get_portfolio()
    if not portfolio["session"]["active"]:
        print("[scalping] No active session. Skipping.")
        return

    event_blocked, event_reason = is_event_blocked(today)
    if event_blocked:
        print(f"[scalping] Event blocked today: {event_reason}. Skipping scan.")
        return

    signals = _detect_orb_signals(WATCHLIST, today)

    sc     = portfolio.get("scalping_capital", {})
    equity = sc.get("equity", 5000.0)
    cash   = sc.get("cash",   5000.0)

    if signals:
        lines = [
            f"📡 <b>ORB SCALPING SCAN — {today}</b> (10:00 AM ET)\n",
            f"⚡ <b>{len(signals)} ORB signal(s) generated</b> — auto-close at 12:00 PM ET\n",
        ]
        for s in signals:
            dir_arrow = "⬆️" if s["direction"] == "long" else "⬇️"
            lines.append(
                f"  {dir_arrow} <b>{s['ticker']}</b> {s['direction'].upper()} @ ${s['entry_price']:.2f}  "
                f"TP=${s['target_price']:.2f}  SL=${s['stop_price']:.2f}"
            )
        lines.append(f"\n<i>Scalping pool: ${equity:,.2f} equity  |  ${cash:,.2f} cash remaining</i>")
        lines.append("<i>Signal cards sent to group. See group for details.</i>")
        broadcast_message("\n".join(lines))
    else:
        broadcast_message(
            f"📡 <b>ORB Scalping Scan — {today}</b>\n\n"
            f"No breakout signals this morning — price inside opening ranges.\n\n"
            f"<i>Scalping pool: ${equity:,.2f}</i>"
        )

    print(f"[scalping] Done. {len(signals)} signal(s) generated.")


if __name__ == "__main__":
    main()
