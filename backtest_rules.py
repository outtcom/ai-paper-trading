"""
Rules-based strategy backtester — 1-year analysis, no LLM calls.

Approximates the two active strategies the system runs:
  1. Swing Strategy  — RSI + MACD + moving averages (proxy for 7-agent pipeline)
  2. ICT FVG Proxy   — RSI pullback into prior-day range (daily proxy; real uses 5-min intraday)

Gap & Go removed: 18.6% win rate, profit factor 0.43, -7.18% in a +23.94% SPY year.
Each strategy runs on a separate $5,000 capital pool, matching the live system.
SPY buy-and-hold is the benchmark.

Usage:
  python backtest_rules.py
  python backtest_rules.py --start 2025-05-01 --end 2026-05-18
"""
import argparse
import json
import math
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools.market_data import get_ohlcv, get_intraday_ohlcv_history

# -- Config -------------------------------------------------------------------

SWING_TICKERS  = ["AAPL", "GOOGL", "NVDA", "MSFT", "AMZN"]
SCALP_TICKERS  = ["AAPL", "GOOGL", "NVDA", "MSFT", "AMZN", "META", "TSLA"]
INITIAL_CAPITAL = 5000.0

# Swing rules
SWING_SL_PCT        = 0.05   # 5% stop loss
SWING_TP_PCT        = 0.10   # 10% take profit
SWING_POSITION_PCT  = 0.20   # 20% of cash per trade
MAX_POSITIONS       = 5
RSI_ENTRY_MIN       = 45
RSI_ENTRY_MAX       = 65
RSI_EXIT_THRESHOLD  = 38

# ICT FVG proxy rules (daily approximation)
FVG_PULLBACK_MIN    = 0.382  # Fib retracement minimum (38.2%)
FVG_PULLBACK_MAX    = 0.786  # OTE zone max (78.6%)
FVG_SL_PCT          = 0.03   # 3% stop (tighter — scalping)
FVG_TP_PCT          = 0.06   # 6% target (2:1 RRR)
FVG_POSITION_PCT    = 0.15


# -- Technical indicators ------------------------------------------------------

def _rsi(closes: list, period: int = 14) -> list:
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    rsi = [None] * period
    gains = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi.append(100 - 100 / (1 + rs))
    return rsi


def _ema(values: list, period: int) -> list:
    if len(values) < period:
        return [None] * len(values)
    result = [None] * (period - 1)
    seed = sum(values[:period]) / period
    result.append(seed)
    k = 2 / (period + 1)
    for v in values[period:]:
        result.append(result[-1] * (1 - k) + v * k)
    return result


def _sma(values: list, period: int) -> list:
    result = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(values[i - period + 1:i + 1]) / period)
    return result


def _macd(closes: list, fast=12, slow=26, signal=9):
    ema_fast   = _ema(closes, fast)
    ema_slow   = _ema(closes, slow)
    macd_line  = [f - s if f is not None and s is not None else None
                  for f, s in zip(ema_fast, ema_slow)]
    valid      = [v for v in macd_line if v is not None]
    sig_raw    = _ema(valid, signal)
    # Re-align signal to full length
    offset     = len(macd_line) - len(valid)
    signal_line = [None] * offset + [None] * (signal - 1) + sig_raw
    signal_line = signal_line[:len(macd_line)]
    histogram  = [m - s if m is not None and s is not None else None
                  for m, s in zip(macd_line, signal_line)]
    return macd_line, signal_line, histogram


def _avg_volume(volumes: list, i: int, window: int = 20) -> float:
    start = max(0, i - window)
    vols  = [v for v in volumes[start:i] if v > 0]
    return sum(vols) / len(vols) if vols else 0


# -- Strategy 1: Swing Trading -------------------------------------------------

def backtest_swing(tickers: list, bars_map: dict, spy_bars: list) -> dict:
    """
    Approximated swing strategy:
    - Buy when: price > 50d SMA, RSI 45-65, MACD histogram > 0, vol > avg
    - Sell when: TP +10%, SL -5%, RSI < 38
    - Max 5 concurrent positions, 20% per position
    """
    cash      = INITIAL_CAPITAL
    positions = {}   # ticker -> {qty, entry, sl, tp}
    trades    = []
    equity_curve = []
    date_index = {b["date"]: i for i, b in enumerate(spy_bars)}

    all_dates = sorted(set(d for bars in bars_map.values() for d in [b["date"] for b in bars]))

    for date in all_dates:
        day_equity = cash

        # Mark open positions to market
        for ticker, pos in list(positions.items()):
            ticker_bars = bars_map.get(ticker, [])
            today_bar   = next((b for b in ticker_bars if b["date"] == date), None)
            if not today_bar:
                continue
            price = today_bar["close"]
            lo    = today_bar["low"]
            hi    = today_bar["high"]

            # Check SL first (intraday low)
            if lo <= pos["sl"]:
                exit_price = pos["sl"]
                pnl = (exit_price - pos["entry"]) * pos["qty"]
                cash += pos["qty"] * exit_price
                trades.append({"ticker": ticker, "date": date, "entry": pos["entry"],
                               "exit": exit_price, "pnl": pnl, "reason": "stop_loss",
                               "days_held": (datetime.strptime(date, "%Y-%m-%d") - datetime.strptime(pos["opened"], "%Y-%m-%d")).days})
                del positions[ticker]
                continue

            # Check TP (intraday high)
            if hi >= pos["tp"]:
                exit_price = pos["tp"]
                pnl = (exit_price - pos["entry"]) * pos["qty"]
                cash += pos["qty"] * exit_price
                trades.append({"ticker": ticker, "date": date, "entry": pos["entry"],
                               "exit": exit_price, "pnl": pnl, "reason": "take_profit",
                               "days_held": (datetime.strptime(date, "%Y-%m-%d") - datetime.strptime(pos["opened"], "%Y-%m-%d")).days})
                del positions[ticker]
                continue

            day_equity += pos["qty"] * price

        # Entry scan (only if room for more positions)
        if len(positions) < MAX_POSITIONS:
            for ticker in tickers:
                if ticker in positions:
                    continue
                bars = bars_map.get(ticker, [])
                idx  = next((i for i, b in enumerate(bars) if b["date"] == date), None)
                if idx is None or idx < 210:   # need enough history
                    continue

                closes  = [b["close"] for b in bars[:idx+1]]
                volumes = [b["volume"] for b in bars[:idx+1]]
                highs   = [b["high"] for b in bars[:idx+1]]
                lows    = [b["low"] for b in bars[:idx+1]]
                price   = closes[-1]
                vol     = volumes[-1]

                rsi_vals   = _rsi(closes)
                sma50      = _sma(closes, 50)
                sma200     = _sma(closes, 200)
                _, _, hist = _macd(closes)

                if any(v is None for v in [rsi_vals[-1], sma50[-1], sma200[-1], hist[-1]]):
                    continue

                rsi       = rsi_vals[-1]
                avg_vol   = _avg_volume(volumes, idx)
                vol_ratio = vol / avg_vol if avg_vol > 0 else 0

                # Entry conditions
                if (price > sma50[-1]             # above 50d MA
                        and price > sma200[-1]     # above 200d MA (bull trend)
                        and RSI_ENTRY_MIN <= rsi <= RSI_ENTRY_MAX
                        and hist[-1] > 0           # MACD bullish
                        and vol_ratio >= 1.2       # above-avg volume
                        and cash > price * 2):     # can afford at least 2 shares
                    alloc = cash * SWING_POSITION_PCT
                    qty   = max(1, int(alloc / price))
                    cost  = qty * price
                    if cost > cash:
                        continue
                    cash -= cost
                    positions[ticker] = {
                        "qty": qty, "entry": price,
                        "sl": round(price * (1 - SWING_SL_PCT), 2),
                        "tp": round(price * (1 + SWING_TP_PCT), 2),
                        "opened": date,
                    }
                    if len(positions) >= MAX_POSITIONS:
                        break

        # RSI-based exit for existing positions
        for ticker in list(positions.keys()):
            bars = bars_map.get(ticker, [])
            idx  = next((i for i, b in enumerate(bars) if b["date"] == date), None)
            if idx is None or idx < 15:
                continue
            closes  = [b["close"] for b in bars[:idx+1]]
            rsi_vals = _rsi(closes)
            if rsi_vals[-1] is not None and rsi_vals[-1] < RSI_EXIT_THRESHOLD:
                price  = closes[-1]
                pos    = positions[ticker]
                pnl    = (price - pos["entry"]) * pos["qty"]
                cash  += pos["qty"] * price
                trades.append({"ticker": ticker, "date": date, "entry": pos["entry"],
                               "exit": price, "pnl": pnl, "reason": "rsi_exit",
                               "days_held": (datetime.strptime(date, "%Y-%m-%d") - datetime.strptime(pos["opened"], "%Y-%m-%d")).days})
                del positions[ticker]

        # Total equity on this day
        total_eq = cash
        for ticker, pos in positions.items():
            bars = bars_map.get(ticker, [])
            bar  = next((b for b in bars if b["date"] == date), None)
            if bar:
                total_eq += pos["qty"] * bar["close"]
        equity_curve.append({"date": date, "equity": round(total_eq, 2)})

    # Close remaining positions at last price
    for ticker, pos in positions.items():
        bars = bars_map.get(ticker, [])
        if bars:
            price = bars[-1]["close"]
            cash += pos["qty"] * price
            trades.append({"ticker": ticker, "date": bars[-1]["date"], "entry": pos["entry"],
                           "exit": price, "pnl": (price - pos["entry"]) * pos["qty"], "reason": "session_end",
                           "days_held": 0})

    return {"cash": cash, "trades": trades, "equity_curve": equity_curve}


# -- Strategy 2: ICT FVG Proxy -------------------------------------------------

def backtest_fvg_proxy(tickers: list, bars_map: dict) -> dict:
    """
    Daily-data ICT FVG proxy:
    - Bullish displacement: day N closes up > 1.5% with vol surge
    - FVG zone: high of bar N-2 to low of bar N (the "gap" in the 3-bar pattern)
    - Entry: day N+1 or N+2 if open/low touches the FVG zone (pullback)
    - Target: +6% from entry (2:1 RRR), Stop: -3%
    Note: real strategy uses 5-min intraday; daily is an approximation.
    """
    cash   = INITIAL_CAPITAL
    trades = []
    equity_curve = []
    active = {}   # ticker -> pending FVG signal

    all_dates = sorted(set(d for bars in bars_map.values() for d in [b["date"] for b in bars]))

    for date in all_dates:
        # Check active signals
        for ticker in list(active.keys()):
            sig  = active[ticker]
            bars = bars_map.get(ticker, [])
            idx  = next((i for i, b in enumerate(bars) if b["date"] == date), None)
            if idx is None:
                continue
            today = bars[idx]
            entry = today["open"]

            # Check if price pulls back into FVG zone
            if sig["fvg_bottom"] <= today["low"] <= sig["fvg_top"]:
                alloc = cash * FVG_POSITION_PCT
                qty   = max(1, int(alloc / entry))
                cost  = qty * entry
                if cost > cash:
                    del active[ticker]
                    continue
                cash -= cost
                sl   = round(entry * (1 - FVG_SL_PCT), 2)
                tp   = round(entry * (1 + FVG_TP_PCT), 2)
                # Use same-day hi/lo to simulate exit
                hi, lo = today["high"], today["low"]
                if lo <= sl:
                    exit_price = sl; reason = "stop_loss"
                elif hi >= tp:
                    exit_price = tp; reason = "take_profit"
                else:
                    exit_price = today["close"]; reason = "eod_close"
                pnl = (exit_price - entry) * qty
                cash += qty * exit_price
                trades.append({
                    "ticker": ticker, "date": date, "entry": entry,
                    "exit": exit_price, "pnl": round(pnl, 2), "reason": reason,
                    "fvg_bottom": sig["fvg_bottom"], "fvg_top": sig["fvg_top"],
                })
                del active[ticker]
            else:
                # Signal expires after 2 days
                sig["days_pending"] = sig.get("days_pending", 0) + 1
                if sig["days_pending"] >= 2:
                    del active[ticker]

        # Detect new FVG setups (3-bar displacement pattern)
        for ticker in tickers:
            if ticker in active:
                continue
            bars = bars_map.get(ticker, [])
            idx  = next((i for i, b in enumerate(bars) if b["date"] == date), None)
            if idx is None or idx < 22:
                continue

            b0, b1, b2 = bars[idx-2], bars[idx-1], bars[idx]
            # Bullish displacement: b2 closes >1.5% above open with above-avg vol
            disp_pct = (b2["close"] - b2["open"]) / b2["open"] * 100
            avg_vol  = _avg_volume([b["volume"] for b in bars], idx)
            if disp_pct > 1.5 and b2["volume"] > avg_vol * 1.3:
                # FVG = b0 high to b2 low (gap in 3-bar pattern)
                fvg_top    = b0["high"]
                fvg_bottom = b2["low"]
                if fvg_bottom < fvg_top:   # valid gap
                    active[ticker] = {
                        "fvg_top": fvg_top,
                        "fvg_bottom": fvg_bottom,
                        "days_pending": 0,
                    }

        # Mark equity
        equity_curve.append({"date": date, "equity": round(cash, 2)})

    return {"cash": cash, "trades": trades, "equity_curve": equity_curve}


# -- Metrics -------------------------------------------------------------------

def _compute_metrics(trades: list, equity_curve: list, initial: float) -> dict:
    if not equity_curve:
        return {}
    final_eq = equity_curve[-1]["equity"]
    total_ret = (final_eq - initial) / initial * 100

    wins   = [t for t in trades if t.get("pnl", 0) > 0]
    losses = [t for t in trades if t.get("pnl", 0) <= 0]
    win_rate   = len(wins) / len(trades) * 100 if trades else 0
    avg_win    = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss   = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    profit_factor = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses and sum(t["pnl"] for t in losses) != 0 else 0

    # Daily returns for Sharpe
    equities = [e["equity"] for e in equity_curve]
    daily_rets = [(equities[i] - equities[i-1]) / equities[i-1] for i in range(1, len(equities))]
    if daily_rets:
        avg_dr  = sum(daily_rets) / len(daily_rets)
        std_dr  = math.sqrt(sum((r - avg_dr)**2 for r in daily_rets) / len(daily_rets))
        sharpe  = (avg_dr / std_dr * math.sqrt(252)) if std_dr > 0 else 0
        neg_rets = [r for r in daily_rets if r < 0]
        down_std = math.sqrt(sum(r**2 for r in neg_rets) / len(neg_rets)) if neg_rets else std_dr
        sortino  = (avg_dr / down_std * math.sqrt(252)) if down_std > 0 else 0
    else:
        sharpe = sortino = 0

    # Max drawdown
    peak = initial
    max_dd = 0
    for e in equities:
        if e > peak:
            peak = e
        dd = (peak - e) / peak * 100
        if dd > max_dd:
            max_dd = dd

    return {
        "total_return_pct": round(total_ret, 2),
        "final_equity": round(final_eq, 2),
        "total_trades": len(trades),
        "win_rate_pct": round(win_rate, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "max_drawdown_pct": round(max_dd, 2),
    }


def _print_results(name: str, metrics: dict, spy_ret: float, trades: list):
    alpha = metrics.get("total_return_pct", 0) - spy_ret
    print(f"\n{'-'*55}")
    print(f"  {name}")
    print(f"{'-'*55}")
    print(f"  Return:         {metrics.get('total_return_pct', 0):+.2f}%")
    print(f"  SPY (B&H):      {spy_ret:+.2f}%")
    print(f"  Alpha vs SPY:   {alpha:+.2f}%")
    print(f"  Final Equity:   ${metrics.get('final_equity', 0):,.2f}  (from $5,000)")
    print(f"  Total Trades:   {metrics.get('total_trades', 0)}")
    print(f"  Win Rate:       {metrics.get('win_rate_pct', 0):.1f}%")
    print(f"  Avg Win:        ${metrics.get('avg_win', 0):+.2f}")
    print(f"  Avg Loss:       ${metrics.get('avg_loss', 0):+.2f}")
    print(f"  Profit Factor:  {metrics.get('profit_factor', 0):.2f}")
    print(f"  Sharpe:         {metrics.get('sharpe', 0):.2f}")
    print(f"  Sortino:        {metrics.get('sortino', 0):.2f}")
    print(f"  Max Drawdown:   {metrics.get('max_drawdown_pct', 0):.2f}%")

    # Top exit reasons
    if trades:
        reasons = {}
        for t in trades:
            r = t.get("reason", "unknown")
            reasons[r] = reasons.get(r, 0) + 1
        print(f"  Exit breakdown: " + "  |  ".join(f"{k}: {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])))


# -- Strategy 3 (real): ICT FVG Intraday Backtest (60 days, 5-min bars) --------

def backtest_fvg_intraday(tickers: list) -> dict:
    """
    60-day ICT FVG + OTE backtest using real 5-min yfinance data.
    Entry: FVG touch or OTE pullback after 9:30–10:00 AM displacement.
    Exit: TP (displacement high/low) or SL (below FVG) or noon auto-close.
    NOTE: scalping_scan.py was removed (strategy replaced by midcap/penny scanners).
    This function is kept for historical reference — returns empty results.
    """
    try:
        from scalping_scan import _find_fvgs, _mark_mitigated, _find_ote_zone, _find_fvg_entry
    except ImportError:
        print("  [FVG intraday] scalping_scan.py removed — FVG backtest not available.")
        return {"cash": INITIAL_CAPITAL, "trades": [], "equity_curve": []}

    RANGE_BARS        = 6     # 9:30–10:00 AM at 5-min = 6 bars
    DISP_MIN_PCT      = 0.30
    STOP_BUFFER_MULT  = 0.10
    MIN_RRR           = 2.0
    POSITION_PCT      = 0.15

    cash         = INITIAL_CAPITAL
    trades       = []
    equity_curve = []

    print(f"  Fetching 60 days of 5-min bars for {len(tickers)} tickers... ", end="", flush=True)
    all_bars: dict = {}
    for ticker in tickers:
        bars_by_date = get_intraday_ohlcv_history(ticker, days_back=60, interval="5m")
        if bars_by_date:
            all_bars[ticker] = bars_by_date
            print(".", end="", flush=True)
    print(f" done ({len(all_bars)} tickers)")

    all_dates = sorted(set(d for bbd in all_bars.values() for d in bbd))

    for date in all_dates:
        for ticker in tickers:
            bars = all_bars.get(ticker, {}).get(date, [])
            if len(bars) < RANGE_BARS + 2:
                continue

            range_bars      = bars[:RANGE_BARS]
            post_range_bars = bars[RANGE_BARS:]

            session_open     = range_bars[0]["open"]
            range_close      = range_bars[-1]["close"]
            displacement_pct = (range_close - session_open) / session_open * 100

            if abs(displacement_pct) < DISP_MIN_PCT:
                continue

            direction = "long" if displacement_pct > 0 else "short"
            disp_high = max(b["high"] for b in range_bars)
            disp_low  = min(b["low"]  for b in range_bars)
            disp_range = disp_high - disp_low

            fvgs = _find_fvgs(range_bars, direction)
            if not fvgs:
                continue

            _mark_mitigated(fvgs, post_range_bars, direction)
            active_fvgs = [f for f in fvgs if not f["mitigated"]]
            if not active_fvgs:
                continue

            ote_low, ote_high = _find_ote_zone(disp_high, disp_low, direction)
            entry_price, best_fvg = _find_fvg_entry(active_fvgs, post_range_bars, direction)

            if entry_price is None:
                current = post_range_bars[-1]["close"] if post_range_bars else range_close
                if ote_low <= current <= ote_high:
                    entry_price = current
                    best_fvg    = None
                else:
                    continue

            # Stop and target
            if best_fvg:
                fvg_size = best_fvg["top"] - best_fvg["bottom"]
                if direction == "long":
                    stop_price = round(best_fvg["bottom"] - fvg_size * STOP_BUFFER_MULT, 2)
                else:
                    stop_price = round(best_fvg["top"]    + fvg_size * STOP_BUFFER_MULT, 2)
            else:
                buffer     = disp_range * 0.02
                stop_price = round(disp_low - buffer if direction == "long" else disp_high + buffer, 2)

            target_price = round(disp_high if direction == "long" else disp_low, 2)

            risk   = abs(entry_price - stop_price)
            reward = abs(target_price - entry_price)
            if risk <= 0 or reward / risk < MIN_RRR:
                continue

            alloc = cash * POSITION_PCT
            qty   = max(1, int(alloc / entry_price))
            cost  = qty * entry_price
            if cost > cash:
                continue

            # Simulate exit using post-entry bars
            entry_idx = next(
                (i for i, b in enumerate(post_range_bars)
                 if b["open"] >= entry_price if direction == "long"
                 else b["open"] <= entry_price),
                0
            )
            exit_price = range_close
            reason     = "noon_close"
            for bar in post_range_bars[entry_idx:]:
                ts = bar["timestamp_et"]
                # Auto-close at noon
                if ts[11:16] >= "12:00":
                    exit_price = bar["open"]
                    reason     = "noon_close"
                    break
                if direction == "long":
                    if bar["low"] <= stop_price:
                        exit_price = stop_price
                        reason     = "stop_loss"
                        break
                    if bar["high"] >= target_price:
                        exit_price = target_price
                        reason     = "take_profit"
                        break
                else:
                    if bar["high"] >= stop_price:
                        exit_price = stop_price
                        reason     = "stop_loss"
                        break
                    if bar["low"] <= target_price:
                        exit_price = target_price
                        reason     = "take_profit"
                        break
            else:
                exit_price = post_range_bars[-1]["close"]
                reason     = "eod_close"

            pnl = (exit_price - entry_price) * qty if direction == "long" \
                  else (entry_price - exit_price) * qty
            cash += pnl
            trades.append({
                "ticker": ticker, "date": date, "direction": direction,
                "entry": entry_price, "exit": exit_price,
                "pnl": round(pnl, 2), "reason": reason,
                "rrr": round(reward / risk, 2),
            })

        equity_curve.append({"date": date, "equity": round(cash, 2)})

    return {"cash": cash, "trades": trades, "equity_curve": equity_curve}


# -- Main ----------------------------------------------------------------------

def main(start: str, end: str):
    print(f"\n{'='*55}")
    print(f"  RULES-BASED STRATEGY BACKTEST")
    print(f"  Period: {start} to {end}")
    print(f"  Capital per strategy: ${INITIAL_CAPITAL:,.0f}")
    print(f"{'='*55}")

    # Fetch all data upfront
    all_tickers = sorted(set(SWING_TICKERS + SCALP_TICKERS + ["SPY"]))
    print(f"\nFetching {len(all_tickers)} tickers... ", end="", flush=True)

    # Fetch more history for indicator warmup (200-day SMA needs 200+ bars before start)
    fetch_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=320)).strftime("%Y-%m-%d")
    bars_map = {}
    for ticker in all_tickers:
        try:
            bars = get_ohlcv(ticker, fetch_start, end)
            if bars:
                bars_map[ticker] = bars
                print(".", end="", flush=True)
        except Exception as e:
            print(f"\n  [!] {ticker}: {e}")
    print(f" done ({len(bars_map)} tickers loaded)")

    spy_bars = bars_map.get("SPY", [])
    # SPY return over the backtest window only
    spy_in_window = [b for b in spy_bars if b["date"] >= start]
    spy_ret = 0.0
    if len(spy_in_window) >= 2:
        spy_ret = (spy_in_window[-1]["close"] - spy_in_window[0]["close"]) / spy_in_window[0]["close"] * 100

    # Filter bars_map to start date for strategies that handle warmup internally
    # (we pass full bars so indicators have enough history)

    print(f"\nSPY benchmark ({start} to {end}): {spy_ret:+.2f}%")

    # Run strategies
    print("\nRunning Swing Strategy...")
    swing_bars  = {t: bars_map[t] for t in SWING_TICKERS if t in bars_map}
    swing_result = backtest_swing(SWING_TICKERS, swing_bars, spy_bars)
    swing_metrics = _compute_metrics(swing_result["trades"], swing_result["equity_curve"], INITIAL_CAPITAL)

    print("Running ICT FVG Proxy Strategy...")
    fvg_bars    = {t: bars_map[t] for t in SCALP_TICKERS if t in bars_map}
    fvg_result  = backtest_fvg_proxy(SCALP_TICKERS, fvg_bars)
    fvg_metrics = _compute_metrics(fvg_result["trades"], fvg_result["equity_curve"], INITIAL_CAPITAL)

    # Print results
    _print_results("SWING STRATEGY (7-agent pipeline proxy)", swing_metrics, spy_ret, swing_result["trades"])
    _print_results("ICT FVG SCALPING (daily proxy -- real uses 5-min)", fvg_metrics, spy_ret, fvg_result["trades"])

    # Combined portfolio
    total_initial  = INITIAL_CAPITAL * 2
    total_final    = swing_metrics.get("final_equity", INITIAL_CAPITAL) + \
                     fvg_metrics.get("final_equity", INITIAL_CAPITAL)
    total_ret      = (total_final - total_initial) / total_initial * 100
    combined_alpha = total_ret - spy_ret
    print(f"\n{'='*55}")
    print(f"  COMBINED PORTFOLIO ($10,000 total)")
    print(f"{'-'*55}")
    print(f"  Total Return:   {total_ret:+.2f}%")
    print(f"  SPY Return:     {spy_ret:+.2f}%")
    print(f"  Combined Alpha: {combined_alpha:+.2f}%")
    print(f"  Final Value:    ${total_final:,.2f}  (from $10,000)")
    print(f"{'='*55}")

    # Save JSON
    os.makedirs(".tmp", exist_ok=True)
    out = {
        "period": {"start": start, "end": end},
        "spy_return_pct": round(spy_ret, 2),
        "swing": {**swing_metrics, "trade_count": len(swing_result["trades"])},
        "ict_fvg_proxy": {**fvg_metrics, "trade_count": len(fvg_result["trades"])},
        "combined_return_pct": round(total_ret, 2),
        "combined_alpha_pct": round(combined_alpha, 2),
    }
    out_path = f".tmp/backtest_rules_{start}_{end}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\nDetailed results saved to {out_path}")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=(datetime.today() - timedelta(days=365)).strftime("%Y-%m-%d"))
    parser.add_argument("--end",   default=datetime.today().strftime("%Y-%m-%d"))
    parser.add_argument("--fvg-intraday", action="store_true",
                        help="Run the 60-day real ICT FVG intraday backtest (5-min bars via yfinance)")
    args = parser.parse_args()

    if args.fvg_intraday:
        print("\n" + "="*55)
        print("  ICT FVG INTRADAY BACKTEST (60 days, real 5-min data)")
        print("="*55)
        result  = backtest_fvg_intraday(SCALP_TICKERS)
        metrics = _compute_metrics(result["trades"], result["equity_curve"], INITIAL_CAPITAL)

        # Fetch SPY for comparison over the same 60-day window
        end60   = datetime.today().strftime("%Y-%m-%d")
        start60 = (datetime.today() - timedelta(days=90)).strftime("%Y-%m-%d")
        try:
            spy_bars = get_ohlcv("SPY", start60, end60)
            spy_in   = [b for b in spy_bars if b["date"] >= (datetime.today() - timedelta(days=60)).strftime("%Y-%m-%d")]
            spy_ret  = (spy_in[-1]["close"] - spy_in[0]["close"]) / spy_in[0]["close"] * 100 if len(spy_in) >= 2 else 0.0
        except Exception:
            spy_ret = 0.0

        _print_results("ICT FVG SCALPING (real 5-min, 60 days)", metrics, spy_ret, result["trades"])

        os.makedirs(".tmp", exist_ok=True)
        out_path = ".tmp/backtest_fvg_intraday.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({**metrics, "trade_count": len(result["trades"]), "spy_return_pct": round(spy_ret, 2)}, f, indent=2)
        print(f"\nDetailed results saved to {out_path}")
    else:
        main(args.start, args.end)
