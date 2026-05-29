"""
10 AM Momentum Continuation Scanner — runs at 10:30 AM ET.
Triggered by GitHub Actions daily (Mon–Fri).

Strategy: Opening Range Breakout Momentum Continuation
  - Opening Range (OR) = 9:30–10:00 AM (first 30 min of regular session)
  - Scan at ~10:30 AM: stocks holding above OR high (long) or below OR low (short)
  - Volume must sustain in the 10:00–10:30 AM confirmation window (>1.2x OR volume)
  - Last confirmation bar must close in upper/lower 40%+ of its range (no reversal wicks)
  - Stop: OR low/high (capped at 2% from entry)
  - Target: entry ± 2× risk (enforced 1.8:1 minimum RRR)
  - Auto-close at 12:00 PM ET via midday_check.py
  - Max 3 concurrent signals from the scalping pool ($5,000 separate capital)
"""
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    STOCKS, INDEX_ETFS,
    MOMENTUM_CONT_OR_MINUTES,
    MOMENTUM_CONT_MAX_EXTENSION_PCT,
    MOMENTUM_CONT_MIN_OR_RANGE_PCT,
    MOMENTUM_CONT_VOL_RATIO,
    MOMENTUM_CONT_MAX_STOP_PCT,
    MOMENTUM_CONT_MIN_RRR,
    MOMENTUM_CONT_MAX_SIGNALS,
    SCALPING_CAPITAL_INIT,
)
from tools.market_data import _yahoo_intraday_ohlcv, _yahoo_direct_ohlcv
from tools.market_regime import is_event_blocked
from tools.session_manager import get_portfolio, add_scalping_signal
from tools.telegram_bot import broadcast_message, send_group_trade_signal


def _detect_momentum_continuation(watchlist: list, today: str) -> list:
    """
    Scan for stocks holding above (long) or below (short) their 9:30–10:00 AM
    opening range with sustained volume at scan time (~10:30 AM).

    Returns list of signal dicts added to portfolio.
    """
    signals   = []
    n_range   = MOMENTUM_CONT_OR_MINUTES // 5   # 30 min / 5 min = 6 bars

    for ticker in watchlist:
        if len(signals) >= MOMENTUM_CONT_MAX_SIGNALS:
            break

        # Equities only — skip crypto and ETFs
        if ticker.endswith("-USD") or ticker in INDEX_ETFS:
            continue

        bars = _yahoo_intraday_ohlcv(ticker, today, "5m")
        if len(bars) < n_range + 2:
            print(f"[momentum] {ticker}: only {len(bars)} bars — skipping")
            continue

        or_bars   = bars[:n_range]             # 9:30–10:00 AM (opening range)
        conf_bars = bars[n_range:n_range * 2]  # 10:00–10:30 AM only (confirmation window)

        if len(conf_bars) < 3:
            print(f"[momentum] {ticker}: only {len(conf_bars)} confirmation bars — skipping")
            continue

        or_high = max(b["high"] for b in or_bars)
        or_low  = min(b["low"]  for b in or_bars)
        or_mid  = (or_high + or_low) / 2
        or_range_pct = (or_high - or_low) / or_mid if or_mid > 0 else 0

        # Skip flat opens (no meaningful range to break)
        if or_range_pct < MOMENTUM_CONT_MIN_OR_RANGE_PCT:
            print(f"[momentum] {ticker}: OR too tight ({or_range_pct:.2%}) — skipping")
            continue

        current = conf_bars[-1]["close"]

        if current < 10:          # skip low-priced stocks
            continue

        # Volume check: confirmation bar volume vs 30-day daily baseline per 5-min bar.
        # Using daily baseline (not OR volume) because the opening rush is always the
        # highest-volume period — comparing against it would filter almost everything.
        try:
            start_d    = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=35)).strftime("%Y-%m-%d")
            daily_bars = _yahoo_direct_ohlcv(ticker, start_d, today)
            recent_v   = [b["volume"] for b in daily_bars[-30:] if b["volume"] > 0]
            baseline   = sum(recent_v) / max(1, len(recent_v)) / 78   # avg volume per 5-min bar
        except Exception:
            baseline = 0

        conf_vol_avg = sum(b["volume"] for b in conf_bars) / len(conf_bars)
        vol_ratio    = conf_vol_avg / baseline if baseline > 0 else 1.0

        if baseline > 0 and vol_ratio < MOMENTUM_CONT_VOL_RATIO:
            print(f"[momentum] {ticker}: volume {vol_ratio:.1f}x baseline < {MOMENTUM_CONT_VOL_RATIO}x — weak momentum")
            continue

        # Determine breakout direction
        direction = None
        last_bar  = conf_bars[-1]
        bar_range = last_bar["high"] - last_bar["low"]

        if current > or_high:
            extension = (current - or_high) / or_high
            if extension > MOMENTUM_CONT_MAX_EXTENSION_PCT:
                print(f"[momentum] {ticker}: too extended above OR ({extension:.1%}) — chasing, skip")
                continue
            # Reject if last candle closed in lower 40% of its range (reversal wick)
            if bar_range > 0 and (last_bar["close"] - last_bar["low"]) / bar_range < 0.40:
                print(f"[momentum] {ticker}: reversal wick on last bar — no long")
                continue
            direction = "long"

        elif current < or_low:
            extension = (or_low - current) / or_low
            if extension > MOMENTUM_CONT_MAX_EXTENSION_PCT:
                print(f"[momentum] {ticker}: too extended below OR ({extension:.1%}) — chasing, skip")
                continue
            if bar_range > 0 and (last_bar["high"] - last_bar["close"]) / bar_range < 0.40:
                print(f"[momentum] {ticker}: reversal wick on last bar — no short")
                continue
            direction = "short"

        if direction is None:
            print(f"[momentum] {ticker}: ${current:.2f} inside OR (${or_low:.2f}–${or_high:.2f}) — no breakout")
            continue

        # Size the stop and target
        entry_price = round(current, 2)
        if direction == "long":
            raw_stop = or_low
            risk = entry_price - raw_stop
            if risk / entry_price > MOMENTUM_CONT_MAX_STOP_PCT:
                raw_stop = entry_price * (1 - MOMENTUM_CONT_MAX_STOP_PCT)
                risk     = entry_price - raw_stop
            stop_price   = round(raw_stop, 2)
            target_price = round(entry_price + risk * 2, 2)
        else:
            raw_stop = or_high
            risk = raw_stop - entry_price
            if risk / entry_price > MOMENTUM_CONT_MAX_STOP_PCT:
                raw_stop = entry_price * (1 + MOMENTUM_CONT_MAX_STOP_PCT)
                risk     = raw_stop - entry_price
            stop_price   = round(raw_stop, 2)
            target_price = round(entry_price - risk * 2, 2)

        if risk <= 0:
            continue

        rrr = round(abs(target_price - entry_price) / risk, 2)
        if rrr < MOMENTUM_CONT_MIN_RRR:
            print(f"[momentum] {ticker}: RRR {rrr:.1f} < {MOMENTUM_CONT_MIN_RRR} — skip")
            continue

        target_pct = round(abs(target_price - entry_price) / entry_price * 100, 2)
        stop_pct   = round(risk / entry_price * 100, 2)

        rationale = (
            f"10AM Momentum {direction.upper()} | "
            f"OR: ${or_low:.2f}–${or_high:.2f} ({or_range_pct:.1%} range) | "
            f"Breakout @ ${entry_price:.2f} | Vol {vol_ratio:.1f}× OR | "
            f"TP={target_pct:.1f}% SL={stop_pct:.1f}% RRR={rrr:.1f} | "
            f"Auto-close noon ET"
        )

        print(f"[momentum] SIGNAL: {ticker} {direction.upper()} @ ${entry_price:.2f}  "
              f"TP=${target_price:.2f} (+{target_pct:.1f}%)  "
              f"SL=${stop_price:.2f} (-{stop_pct:.1f}%)  "
              f"vol={vol_ratio:.1f}×  RRR={rrr:.1f}")

        signal_dict = {
            "signal_type":    "scalping_momentum",
            "ticker":         ticker,
            "direction":      direction,
            "entry_price":    entry_price,
            "target_price":   target_price,
            "stop_price":     stop_price,
            "target_pct":     target_pct,
            "stop_pct":       stop_pct,
            "rrr":            rrr,
            "rationale":      rationale,
            "generated_date": today,
            "status":         "open",
            "id":             f"{ticker}_{today}_{direction}",
        }

        add_scalping_signal(signal_dict)

        group_card = {
            "ticker":        ticker,
            "signal_type":   "scalping_momentum",
            "direction":     direction,
            "entry_price":   entry_price,
            "target_price":  target_price,
            "stop_price":    stop_price,
            "target_pct":    target_pct,
            "stop_pct":      stop_pct,
            "rrr":           rrr,
            "allocated_usd": 0,   # filled by add_scalping_signal
            "qty":           0,
            "session_day":   1,
            "total_days":    60,
            "rationale":     rationale,
        }
        send_group_trade_signal(group_card)

        signals.append(signal_dict)

    return signals


def main():
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    print(f"\n[momentum] ========== 10AM Momentum Continuation Scan {today} ==========")

    portfolio = get_portfolio()
    if not portfolio["session"]["active"]:
        print("[momentum] No active session. Skipping.")
        return

    event_blocked, event_reason = is_event_blocked(today)
    if event_blocked:
        print(f"[momentum] Event blocked today: {event_reason}. Skipping scan.")
        return

    signals = _detect_momentum_continuation(STOCKS, today)

    sc     = portfolio.get("scalping_capital", {})
    equity = sc.get("equity", SCALPING_CAPITAL_INIT)
    cash   = sc.get("cash",   SCALPING_CAPITAL_INIT)

    if signals:
        lines = [
            f"⚡ <b>10AM MOMENTUM SCAN — {today}</b>\n",
            f"<b>{len(signals)} breakout(s) confirmed</b> — auto-close 12:00 PM ET\n",
        ]
        for s in signals:
            arrow = "⬆️" if s["direction"] == "long" else "⬇️"
            lines.append(
                f"  {arrow} <b>{s['ticker']}</b> {s['direction'].upper()} @ ${s['entry_price']:.2f}  "
                f"TP=${s['target_price']:.2f} (+{s['target_pct']:.1f}%)  "
                f"SL=${s['stop_price']:.2f} (-{s['stop_pct']:.1f}%)  "
                f"RRR={s['rrr']:.1f}"
            )
        lines.append(f"\n<i>Momentum pool: ${equity:,.2f} equity  |  ${cash:,.2f} cash</i>")
        lines.append("<i>Signal cards sent to group.</i>")
        broadcast_message("\n".join(lines))
    else:
        broadcast_message(
            f"⚡ <b>10AM Momentum Scan — {today}</b>\n\n"
            f"No breakouts — tickers inside opening range or volume insufficient.\n\n"
            f"<i>Momentum pool: ${equity:,.2f}</i>"
        )

    print(f"[momentum] Done. {len(signals)} signal(s) generated.")


if __name__ == "__main__":
    main()
