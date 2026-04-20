"""
Pre-market gap scanner — runs at 7:00 AM ET (30 min before morning session).
Triggered by GitHub Actions daily.

Scans all watchlist tickers for significant pre-market gaps vs prior close.
Also detects gap-and-go day trade signals (paper-only, no capital allocated).
"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    WATCHLIST,
    DAY_TRADE_GAP_MIN_PCT, DAY_TRADE_VOLUME_RATIO_MIN,
    GAP_AND_GO_TARGET_PCT, GAP_AND_GO_STOP_PCT,
)
from tools.market_data import _yahoo_direct_ohlcv, _finnhub_get
from tools.market_regime import get_premarket_gaps, is_event_blocked
from tools.session_manager import get_portfolio, add_day_trade_signal
from tools.telegram_bot import broadcast_message, send_group_trade_signal

GAP_THRESHOLD = 2.0   # percent — flag if gap exceeds this


def _detect_gap_and_go_signals(gaps: dict, today: str) -> list:
    """
    Scan gap data for gap-and-go day trade signals.
    A signal is generated when gap >= DAY_TRADE_GAP_MIN_PCT and volume >= DAY_TRADE_VOLUME_RATIO_MIN.
    Returns list of signal dicts added to portfolio.
    """
    from datetime import timedelta
    signals = []

    for ticker, data in gaps.items():
        if ticker.endswith("-USD"):
            continue  # skip crypto for gap-and-go
        gap_pct = data.get("gap_pct", 0)
        if abs(gap_pct) < DAY_TRADE_GAP_MIN_PCT:
            continue

        entry_price = data.get("current") or data.get("prev_close")
        if not entry_price or entry_price <= 0:
            continue

        # Fetch 30-day average volume via Yahoo direct HTTP
        try:
            end   = today
            start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=35)).strftime("%Y-%m-%d")
            bars  = _yahoo_direct_ohlcv(ticker, start, end)
            if len(bars) >= 5:
                avg_vol = sum(b["volume"] for b in bars[-30:] if b["volume"] > 0) / max(1, sum(1 for b in bars[-30:] if b["volume"] > 0))
            else:
                avg_vol = 0
        except Exception:
            avg_vol = 0

        # Finnhub current volume (field 'v' in quote)
        try:
            quote = _finnhub_get(f"/quote?symbol={ticker}")
            current_vol = quote.get("v", 0) or 0
        except Exception:
            current_vol = 0

        vol_ratio = (current_vol / avg_vol) if avg_vol > 0 else 0

        if vol_ratio < DAY_TRADE_VOLUME_RATIO_MIN and avg_vol > 0:
            print(f"[premarket] {ticker} gap {gap_pct:+.1f}% — volume ratio {vol_ratio:.2f}x < {DAY_TRADE_VOLUME_RATIO_MIN}x, skipping signal")
            continue

        target = round(entry_price * (1 + GAP_AND_GO_TARGET_PCT / 100), 2)
        stop   = round(entry_price * (1 - GAP_AND_GO_STOP_PCT  / 100), 2)

        signal = {
            "id":               f"DTS-{today}-{ticker}-gap",
            "ticker":           ticker,
            "signal_type":      "gap_and_go",
            "generated_date":   today,
            "entry_price":      round(entry_price, 2),
            "target_price":     target,
            "target_pct":       GAP_AND_GO_TARGET_PCT,
            "stop_price":       stop,
            "stop_pct":         GAP_AND_GO_STOP_PCT,
            "status":           "open",
            "exit_price":       None,
            "exit_date":        None,
            "pnl_pct":          None,
            "outcome":          None,
            "auto_close_date":  today,
            "rationale":        f"Pre-market gap {gap_pct:+.1f}% (vol ratio {vol_ratio:.1f}x)"
        }

        add_day_trade_signal(signal)
        send_group_trade_signal(signal)
        signals.append(signal)
        print(f"[premarket] Gap-and-go signal: {ticker} {gap_pct:+.1f}% @ ${entry_price:.2f}  TP=${target:.2f}  SL=${stop:.2f}")

    return signals


def main():
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    print(f"\n[premarket] ========== Pre-Market Gap Scanner {today} ==========")

    portfolio = get_portfolio()
    if not portfolio["session"]["active"]:
        print("[premarket] No active session. Skipping.")
        return

    event_blocked, event_reason = is_event_blocked(today)
    if event_blocked:
        broadcast_message(
            f"📅 <b>Pre-Market Alert — {today}</b>\n\n"
            f"⚠️ Today's morning session will be blocked:\n"
            f"🚫 {event_reason}\n\n"
            f"<i>No trade analysis will run today. Staying in cash.</i>"
        )
        print(f"[premarket] Event blocked today: {event_reason}")
        return

    gaps = get_premarket_gaps(WATCHLIST, gap_threshold=GAP_THRESHOLD / 100)

    # Detect gap-and-go signals before formatting the summary
    gap_signals = _detect_gap_and_go_signals(gaps, today)

    flagged   = {t: g for t, g in gaps.items() if g.get("flagged")}
    unflagged = {t: g for t, g in gaps.items() if not g.get("flagged") and "gap_pct" in g}

    if not flagged and not unflagged:
        print("[premarket] No gap data available. Skipping alert.")
        return

    lines = [f"📡 <b>PRE-MARKET SCAN — {today}</b> (30 min to open)\n"]

    if flagged:
        lines.append(f"⚠️ <b>Significant Gaps (&gt;{GAP_THRESHOLD}%):</b>")
        for ticker, data in sorted(flagged.items(), key=lambda x: abs(x[1].get("gap_pct", 0)), reverse=True):
            pct   = data.get("gap_pct", 0)
            prev  = data.get("prev_close", 0)
            curr  = data.get("current", 0)
            direction = "⬆️" if pct > 0 else "⬇️"
            sign  = "+" if pct > 0 else ""
            lines.append(
                f"  {direction} <b>{ticker}</b>: {sign}{pct:.1f}%  "
                f"(prev close ${prev:.2f} → now ${curr:.2f})"
            )
        lines.append(
            f"\n<i>⚠️ Gapped tickers may have stale AI thesis. "
            f"The morning session will recalculate — approve carefully.</i>\n"
        )
    else:
        lines.append(f"✅ <b>No significant gaps</b> (all within ±{GAP_THRESHOLD}%)\n")

    if unflagged:
        lines.append("<b>All other tickers:</b>")
        for ticker, data in unflagged.items():
            pct  = data.get("gap_pct", 0)
            sign = "+" if pct > 0 else ""
            lines.append(f"  {ticker}: {sign}{pct:.1f}%")

    if gap_signals:
        lines.append(f"\n📡 <b>Gap-and-Go Signals:</b> {len(gap_signals)} generated (paper only — see group)")

    session_day = portfolio["session"].get("current_day", "?")
    total_days  = portfolio["session"].get("total_days", 22)
    equity      = portfolio.get("equity", portfolio["initial_capital"])
    lines.append(f"\n<i>Day {session_day}/{total_days}  |  Equity ${equity:,.2f}</i>")
    lines.append("<i>Morning session analysis starts in 30 min.</i>")

    broadcast_message("\n".join(lines))
    print(f"[premarket] Scan sent. {len(flagged)} tickers flagged, {len(gap_signals)} gap-and-go signals.")


if __name__ == "__main__":
    main()
