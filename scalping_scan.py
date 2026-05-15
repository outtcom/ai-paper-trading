"""
ICT FVG Scalping Scanner — runs at 10:00 AM ET (30 min after market open).
Triggered by GitHub Actions daily (Mon–Fri).

Strategy: ICT Fair Value Gap (FVG) + Optimal Trade Entry (OTE)
  - Detects institutional displacement during the NY Open Kill Zone (9:30–10:00 AM)
  - Finds Fair Value Gaps (3-candle imbalances) created during the displacement
  - Enters when price pulls back to an unmitigated FVG or the OTE Fibonacci zone
  - Stop: just beyond the FVG boundary (tight, price-derived)
  - Target: displacement swing high/low (liquidity pool)
  - Minimum 2:1 RRR enforced — no signal without sufficient reward
  - Auto-close at 12:00 PM ET via midday_check.py
"""
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    WATCHLIST,
    SCALPING_RANGE_MINUTES,
    SCALPING_DISPLACEMENT_MIN_PCT,
    SCALPING_FVG_MIN_GAP_PCT,
    SCALPING_OTE_LOW_FIBO,
    SCALPING_OTE_HIGH_FIBO,
    SCALPING_MIN_RRR,
    SCALPING_STOP_BUFFER_MULT,
    SCALPING_VOL_RATIO_MIN,
)
from tools.market_data import _yahoo_intraday_ohlcv, _yahoo_direct_ohlcv, get_latest_price
from tools.market_regime import is_event_blocked
from tools.session_manager import get_portfolio, add_scalping_signal
from tools.telegram_bot import broadcast_message, send_group_trade_signal


def _find_fvgs(bars: list, direction: str) -> list:
    """
    Find all Fair Value Gaps (FVGs) in a bar sequence that align with the given direction.

    Bullish FVG (created during up-move): bars[i-1].high < bars[i+1].low
      - The gap between the prev candle's high and the next candle's low is untraded
      - Price left an imbalance moving up — it will return to fill it on pullback

    Bearish FVG (created during down-move): bars[i-1].low > bars[i+1].high
      - The gap between the prev candle's low and the next candle's high is untraded

    Returns FVGs sorted oldest-first. Only returns FVGs that match direction.
    """
    fvgs = []
    for i in range(1, len(bars) - 1):
        prev, curr, nxt = bars[i - 1], bars[i], bars[i + 1]

        if direction == "long":
            if prev["high"] < nxt["low"]:
                fvg_bottom = prev["high"]
                fvg_top    = nxt["low"]
                gap_pct    = (fvg_top - fvg_bottom) / fvg_bottom * 100
                if gap_pct >= SCALPING_FVG_MIN_GAP_PCT:
                    fvgs.append({
                        "type":      "bullish",
                        "top":       round(fvg_top,    4),
                        "bottom":    round(fvg_bottom, 4),
                        "midpoint":  round((fvg_top + fvg_bottom) / 2, 4),
                        "gap_pct":   round(gap_pct, 4),
                        "bar_index": i,
                        "timestamp": curr["timestamp_et"],
                        "mitigated": False,
                    })

        else:  # direction == "short"
            if prev["low"] > nxt["high"]:
                fvg_bottom = nxt["high"]
                fvg_top    = prev["low"]
                gap_pct    = (fvg_top - fvg_bottom) / fvg_bottom * 100
                if gap_pct >= SCALPING_FVG_MIN_GAP_PCT:
                    fvgs.append({
                        "type":      "bearish",
                        "top":       round(fvg_top,    4),
                        "bottom":    round(fvg_bottom, 4),
                        "midpoint":  round((fvg_top + fvg_bottom) / 2, 4),
                        "gap_pct":   round(gap_pct, 4),
                        "bar_index": i,
                        "timestamp": curr["timestamp_et"],
                        "mitigated": False,
                    })
    return fvgs


def _mark_mitigated(fvgs: list, subsequent_bars: list, direction: str) -> list:
    """
    Mark FVGs as mitigated if subsequent bars have traded completely through them.
    A bullish FVG is mitigated when a bar closes below fvg_bottom.
    A bearish FVG is mitigated when a bar closes above fvg_top.
    """
    for bar in subsequent_bars:
        for fvg in fvgs:
            if fvg["mitigated"]:
                continue
            if direction == "long" and bar["close"] < fvg["bottom"]:
                fvg["mitigated"] = True
            elif direction == "short" and bar["close"] > fvg["top"]:
                fvg["mitigated"] = True
    return fvgs


def _find_ote_zone(disp_high: float, disp_low: float, direction: str) -> tuple:
    """
    Compute the Optimal Trade Entry (OTE) zone using Fibonacci retracement.
    For bullish: OTE is 61.8%–78.6% pullback from the displacement high.
    For bearish: OTE is 61.8%–78.6% bounce from the displacement low.
    Returns (ote_low, ote_high).
    """
    disp_range = disp_high - disp_low
    if direction == "long":
        ote_low  = disp_high - SCALPING_OTE_HIGH_FIBO * disp_range
        ote_high = disp_high - SCALPING_OTE_LOW_FIBO  * disp_range
    else:
        ote_low  = disp_low + SCALPING_OTE_LOW_FIBO   * disp_range
        ote_high = disp_low + SCALPING_OTE_HIGH_FIBO  * disp_range
    return round(ote_low, 4), round(ote_high, 4)


def _find_fvg_entry(fvgs: list, post_range_bars: list, direction: str):
    """
    Walk post-range bars to find the most recent bar where price touched an
    unmitigated FVG zone. Returns (entry_price, matched_fvg) or (None, None).

    For bullish: a bar's low touches the FVG zone (bar.low <= fvg_top) and
      the bar's close stays at or above fvg_bottom (FVG not fully mitigated).
    For bearish: a bar's high touches the FVG zone (bar.high >= fvg_bottom)
      and the bar's close stays at or below fvg_top.
    """
    entry_price  = None
    matched_fvg  = None

    for bar in post_range_bars:
        for fvg in fvgs:
            if fvg["mitigated"]:
                continue
            if direction == "long":
                touches_zone  = bar["low"]  <= fvg["top"]
                not_through   = bar["close"] >= fvg["bottom"]
                if touches_zone and not_through:
                    entry_price = bar["close"]
                    matched_fvg = fvg
                elif bar["close"] < fvg["bottom"]:
                    fvg["mitigated"] = True
            else:
                touches_zone  = bar["high"] >= fvg["bottom"]
                not_through   = bar["close"] <= fvg["top"]
                if touches_zone and not_through:
                    entry_price = bar["close"]
                    matched_fvg = fvg
                elif bar["close"] > fvg["top"]:
                    fvg["mitigated"] = True

    return entry_price, matched_fvg


def _detect_ict_signals(watchlist: list, today: str) -> list:
    """
    Scan watchlist for ICT Fair Value Gap + OTE scalping signals.
    Returns list of signal dicts added to portfolio.
    """
    signals      = []
    n_range_bars = SCALPING_RANGE_MINUTES // 5   # 6 bars at 5-min resolution

    for ticker in watchlist:
        if ticker.endswith("-USD"):
            continue  # no crypto scalping

        bars = _yahoo_intraday_ohlcv(ticker, today, "5m")
        if len(bars) < n_range_bars + 1:
            print(f"[scalping] {ticker}: only {len(bars)} bars — insufficient, skipping")
            continue

        range_bars     = bars[:n_range_bars]        # 9:30–10:00 AM (bars 0–5)
        post_range_bars = bars[n_range_bars:]        # 10:00 AM onwards

        # ── Step 1: Displacement analysis ───────────────────────────────────
        session_open    = range_bars[0]["open"]
        range_close     = range_bars[-1]["close"]
        displacement_pct = (range_close - session_open) / session_open * 100

        if abs(displacement_pct) < SCALPING_DISPLACEMENT_MIN_PCT:
            print(f"[scalping] {ticker}: displacement {displacement_pct:+.2f}% too small "
                  f"(min {SCALPING_DISPLACEMENT_MIN_PCT:.2f}%) — chop, skipping")
            continue

        direction       = "long" if displacement_pct > 0 else "short"
        disp_high       = max(b["high"] for b in range_bars)
        disp_low        = min(b["low"]  for b in range_bars)
        disp_range      = disp_high - disp_low

        # ── Step 2: Find FVGs during displacement phase ──────────────────────
        fvgs = _find_fvgs(range_bars, direction)
        if not fvgs:
            print(f"[scalping] {ticker}: {direction} displacement {displacement_pct:+.2f}% "
                  f"but no FVGs found in opening range — skipping")
            continue

        # Mark FVGs that subsequent bars have already traded through
        _mark_mitigated(fvgs, post_range_bars, direction)
        active_fvgs = [f for f in fvgs if not f["mitigated"]]
        if not active_fvgs:
            print(f"[scalping] {ticker}: all {len(fvgs)} FVG(s) mitigated — skipping")
            continue

        # ── Step 3: OTE zone ─────────────────────────────────────────────────
        ote_low, ote_high = _find_ote_zone(disp_high, disp_low, direction)

        # ── Step 4: Check if post-range bars touched a FVG ──────────────────
        entry_price, best_fvg = _find_fvg_entry(active_fvgs, post_range_bars, direction)

        # ── Step 5: OTE fallback ─────────────────────────────────────────────
        entry_method = "fvg"
        if entry_price is None:
            # No FVG touch yet — check if current price is in OTE zone
            current = post_range_bars[-1]["close"] if post_range_bars else range_close
            if ote_low <= current <= ote_high:
                entry_price = current
                best_fvg    = None
                entry_method = "ote"
            else:
                print(f"[scalping] {ticker}: {direction} | {len(active_fvgs)} active FVG(s) "
                      f"but price not touching (OTE ${ote_low:.2f}–${ote_high:.2f}) — waiting")
                continue

        # ── Step 6: Compute stop and target ──────────────────────────────────
        if best_fvg:
            fvg_size   = best_fvg["top"] - best_fvg["bottom"]
            if direction == "long":
                stop_price = round(best_fvg["bottom"] - fvg_size * SCALPING_STOP_BUFFER_MULT, 2)
            else:
                stop_price = round(best_fvg["top"]    + fvg_size * SCALPING_STOP_BUFFER_MULT, 2)
        else:
            # OTE entry — stop below/above the displacement extreme
            buffer = disp_range * 0.02
            stop_price = round(
                disp_low  - buffer if direction == "long" else disp_high + buffer, 2
            )

        target_price = round(disp_high if direction == "long" else disp_low, 2)

        # ── Step 7: RRR gate ─────────────────────────────────────────────────
        risk   = abs(entry_price - stop_price)
        reward = abs(target_price - entry_price)
        if risk <= 0:
            print(f"[scalping] {ticker}: zero risk (entry={entry_price}, stop={stop_price}) — skipping")
            continue
        rrr = reward / risk
        if rrr < SCALPING_MIN_RRR:
            print(f"[scalping] {ticker}: RRR {rrr:.1f} < {SCALPING_MIN_RRR} minimum — skipping")
            continue

        # ── Step 8: Volume confirmation ───────────────────────────────────────
        breakout_bar = post_range_bars[0] if post_range_bars else range_bars[-1]
        cur_vol      = breakout_bar["volume"]
        try:
            start      = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=35)).strftime("%Y-%m-%d")
            daily_bars = _yahoo_direct_ohlcv(ticker, start, today)
            recent     = [b["volume"] for b in daily_bars[-30:] if b["volume"] > 0]
            baseline   = sum(recent) / max(1, len(recent)) / 78
        except Exception:
            baseline = 0
        vol_ratio = (cur_vol / baseline) if baseline > 0 else 1.0

        if baseline > 0 and vol_ratio < SCALPING_VOL_RATIO_MIN:
            print(f"[scalping] {ticker}: low volume ({vol_ratio:.1f}x < {SCALPING_VOL_RATIO_MIN}x) — skipping")
            continue

        # ── Build signal ──────────────────────────────────────────────────────
        target_pct = round((target_price - entry_price) / entry_price * 100, 2)
        stop_pct   = round(abs(entry_price - stop_price) / entry_price * 100, 2)

        fvg_desc = (f"${best_fvg['bottom']:.2f}–${best_fvg['top']:.2f}"
                    if best_fvg else f"OTE ${ote_low:.2f}–${ote_high:.2f}")
        rationale = (
            f"ICT {'FVG' if entry_method == 'fvg' else 'OTE'} {direction} | "
            f"disp {displacement_pct:+.2f}% | "
            f"zone {fvg_desc} | "
            f"RRR {rrr:.1f}:1 | vol {vol_ratio:.1f}x"
        )

        signal = {
            "id":                 f"DTS-{today}-{ticker}-scalp",
            "ticker":             ticker,
            "signal_type":        "scalping_fvg",
            "direction":          direction,
            "generated_date":     today,
            "entry_price":        round(entry_price, 2),
            "target_price":       target_price,
            "target_pct":         target_pct,
            "stop_price":         stop_price,
            "stop_pct":           stop_pct,
            "displacement_high":  round(disp_high, 2),
            "displacement_low":   round(disp_low,  2),
            "displacement_pct":   round(displacement_pct, 2),
            "fvg_top":            round(best_fvg["top"],    2) if best_fvg else None,
            "fvg_bottom":         round(best_fvg["bottom"], 2) if best_fvg else None,
            "ote_zone_low":       round(ote_low,  2),
            "ote_zone_high":      round(ote_high, 2),
            "entry_method":       entry_method,
            "rrr":                round(rrr, 2),
            "status":             "open",
            "exit_price":         None,
            "exit_date":          None,
            "pnl_pct":            None,
            "pnl_usd":            None,
            "outcome":            None,
            "auto_close_date":    today,
            "rationale":          rationale,
        }

        add_scalping_signal(signal)
        send_group_trade_signal(signal)
        signals.append(signal)
        print(f"[scalping] Signal: {ticker} ICT {entry_method.upper()} {direction.upper()} "
              f"@ ${entry_price:.2f}  TP=${target_price:.2f} (+{target_pct:.2f}%)  "
              f"SL=${stop_price:.2f} (-{stop_pct:.2f}%)  RRR={rrr:.1f}  vol={vol_ratio:.1f}x")

    return signals


def main():
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    print(f"\n[scalping] ========== ICT FVG Scalping Scan {today} ==========")

    portfolio = get_portfolio()
    if not portfolio["session"]["active"]:
        print("[scalping] No active session. Skipping.")
        return

    event_blocked, event_reason = is_event_blocked(today)
    if event_blocked:
        print(f"[scalping] Event blocked today: {event_reason}. Skipping scan.")
        return

    signals = _detect_ict_signals(WATCHLIST, today)

    sc     = portfolio.get("scalping_capital", {})
    equity = sc.get("equity", 5000.0)
    cash   = sc.get("cash",   5000.0)

    if signals:
        lines = [
            f"📡 <b>ICT FVG SCALPING SCAN — {today}</b> (NY Open Kill Zone)\n",
            f"⚡ <b>{len(signals)} ICT signal(s) generated</b> — auto-close at 12:00 PM ET\n",
        ]
        for s in signals:
            dir_arrow = "⬆️" if s["direction"] == "long" else "⬇️"
            lines.append(
                f"  {dir_arrow} <b>{s['ticker']}</b> {s['direction'].upper()} @ ${s['entry_price']:.2f}  "
                f"TP=${s['target_price']:.2f}  SL=${s['stop_price']:.2f}  RRR={s['rrr']:.1f}"
            )
        lines.append(f"\n<i>Scalping pool: ${equity:,.2f} equity  |  ${cash:,.2f} cash remaining</i>")
        lines.append("<i>Signal cards sent to group. See group for details.</i>")
        broadcast_message("\n".join(lines))
    else:
        broadcast_message(
            f"📡 <b>ICT FVG Scalping Scan — {today}</b>\n\n"
            f"No signals — tickers in chop or FVGs not yet touched.\n\n"
            f"<i>Scalping pool: ${equity:,.2f}</i>"
        )

    print(f"[scalping] Done. {len(signals)} signal(s) generated.")


if __name__ == "__main__":
    main()
