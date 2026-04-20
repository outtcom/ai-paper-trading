"""
Morning session entry point.
Triggered by GitHub Actions at 7:30 AM ET on weekdays.

Flow:
  1.  Start/check the 10-day session
  2.  Circuit breaker — halt if peak drawdown > 15% or daily loss > 3%
  3.  FOMC / CPI / NFP auto-block — no trades on macro event days
  4.  VIX check — EXTREME → skip. Apply VIX sizing multiplier.
  4b. VIX rate-of-change filter — spike >20% over 5d → additional 0.5× cut
  5.  Market regime overlay — bearish (SPY < 200d MA) → halve size further
  5b. HYG credit spread check — risk-off signal → cap sizing at 0.5×
  6.  Concurrent position limit — skip if already at MAX_CONCURRENT_POSITIONS
  7.  Portfolio heat check — skip if >75% capital already deployed
  8.  Earnings check — block tickers with earnings within 5 days or reported within 2 days
  9.  Sector strength ranking — fetch sector ETF momentum before analysis
  10. Run 7-agent pipeline on all watchlist tickers (stocks + crypto)
  11. Volume confirmation filter — discard low-volume BUY signals
  12. Portfolio beta cap — skip if adding candidate exceeds 1.5× weighted beta
  13. Rank candidates: conviction + sector bonus → pick best
  14. Send Telegram approval card; poll 60 min
  15. If approved: re-fetch live price, execute paper trade, log journal entry

Usage:
  python morning_session.py
"""
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    WATCHLIST, APPROVAL_TIMEOUT_SECONDS,
    MAX_CONCURRENT_POSITIONS, MAX_PORTFOLIO_HEAT, MIN_VOLUME_RATIO,
    BEARISH_REGIME_MULTIPLIER, TICKER_SECTOR, SECTOR_MAP,
    TICKER_BETA, MAX_PORTFOLIO_BETA, VIX_ROC_THRESHOLD,
    ALLOW_SHORT_SELLING, SHORT_TP_PCT, SHORT_SL_PCT,
    SECTOR_TILT_TOP_MULT, SECTOR_TILT_BOTTOM_MULT,
    DAY_TRADE_VOLUME_RATIO_MIN, MOMENTUM_NEAR_HIGH_PCT,
    MOMENTUM_TARGET_PCT, MOMENTUM_STOP_PCT,
)
from orchestrator import run_pipeline
from tools.market_data import get_latest_price, get_ohlcv, _yahoo_direct_ohlcv
from tools.market_regime import (
    get_vix_multiplier, get_market_trend, has_earnings_soon, is_event_blocked,
    get_vix_roc, get_hyg_signal, had_earnings_recently,
)
from tools.sector_analysis import get_sector_strength, get_sector_bonus, format_sector_heatmap
from tools.session_manager import (
    add_journal_entry,
    add_open_order,
    add_day_trade_signal,
    check_circuit_breaker,
    get_portfolio,
    get_session_day,
    is_session_active,
    open_position,
    record_equity,
    set_spy_start_price,
    start_session,
    update_open_order,
)
from tools.telegram_bot import (
    poll_for_response, send_approval_request, send_message,
    broadcast_message, send_group_trade_signal,
)

_CONVICTION_RANK = {"high": 3, "medium": 2, "low": 1}


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

def _portfolio_heat(portfolio: dict) -> float:
    """Return fraction of capital currently deployed in open positions (0–1)."""
    total = portfolio.get("equity", portfolio["initial_capital"])
    cash  = portfolio.get("cash", total)
    if total <= 0:
        return 0.0
    return round(1 - cash / total, 4)


def _has_volume_confirmation(ticker: str) -> bool:
    """
    Return True if the ticker's 3-day avg volume >= MIN_VOLUME_RATIO × 20d avg.
    Crypto and low-data tickers always pass.
    """
    if MIN_VOLUME_RATIO <= 0 or "-USD" in ticker:
        return True
    try:
        _now  = datetime.now(ZoneInfo("America/New_York"))
        end   = _now.strftime("%Y-%m-%d")
        start = (_now - timedelta(days=35)).strftime("%Y-%m-%d")
        bars  = get_ohlcv(ticker, start, end)
        volumes = [b.get("volume", 0) for b in bars if b.get("volume", 0) > 0]
        if len(volumes) < 10:
            return True
        avg_20d   = sum(volumes[-20:]) / min(20, len(volumes))
        avg_3d    = sum(volumes[-3:]) / 3
        passes    = avg_3d >= avg_20d * MIN_VOLUME_RATIO
        if not passes:
            print(f"[morning] {ticker} volume filter: 3d avg {avg_3d:,.0f} < {MIN_VOLUME_RATIO}× 20d avg {avg_20d:,.0f}")
        return passes
    except Exception:
        return True   # never block on error


def _is_same_sector_open(ticker: str, portfolio: dict) -> bool:
    """
    Return True if an open position already exists in the same sector.
    Prevents stacking correlated names (e.g. NVDA while MSFT is open).
    """
    sector = TICKER_SECTOR.get(ticker, "Unknown")
    if sector in ("Unknown", "Crypto"):
        return False
    for open_ticker in portfolio.get("positions", {}):
        if TICKER_SECTOR.get(open_ticker) == sector:
            print(f"[morning] {ticker} blocked — sector '{sector}' already has open position in {open_ticker}")
            return True
    return False


def _portfolio_beta(portfolio: dict, candidate_ticker: str, candidate_usd: float) -> float:
    """
    Return the weighted average portfolio beta if the candidate position is added.
    Crypto positions are excluded (different risk profile).
    Uses TICKER_BETA from config; defaults to 1.0 for unknown tickers.
    """
    total_equity = portfolio.get("equity", portfolio.get("initial_capital", 5000))
    if total_equity <= 0:
        return 1.0

    weighted_beta = 0.0
    total_weight  = 0.0

    for ticker, pos in portfolio.get("positions", {}).items():
        if "-USD" in ticker:
            continue
        beta = TICKER_BETA.get(ticker, 1.0)
        try:
            price     = get_latest_price(ticker)
            pos_value = pos["qty"] * price
        except Exception:
            pos_value = pos.get("cost_basis", 0)
        weight         = pos_value / total_equity
        weighted_beta += beta * weight
        total_weight  += weight

    if candidate_ticker and "-USD" not in candidate_ticker and candidate_usd > 0:
        beta           = TICKER_BETA.get(candidate_ticker, 1.0)
        weight         = candidate_usd / total_equity
        weighted_beta += beta * weight
        total_weight  += weight

    return round(weighted_beta, 2) if total_weight > 0 else 1.0


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def _analyze_all(date: str, session_portfolio: dict, earnings_blocked: set = None) -> dict:
    """
    Run dry-run pipeline for every watchlist ticker.
    Pre-filters definitionally ineligible tickers before any LLM calls:
      - earnings-blocked tickers
      - tickers in the same sector as an already-open position
      - tickers that fail the volume confirmation check
    Passes real session portfolio so fund_manager prices against actual cash.
    Returns {ticker: state}.
    """
    if earnings_blocked is None:
        earnings_blocked = set()

    # Build a clean portfolio snapshot for the fund manager
    fm_portfolio = {
        "cash":      session_portfolio.get("cash", 5000),
        "equity":    session_portfolio.get("equity", 5000),
        "positions": list(session_portfolio.get("positions", {}).keys()),
    }
    results = {}
    for ticker in WATCHLIST:
        # ── Pre-filter: skip definitionally ineligible tickers ────────────
        if ticker in earnings_blocked:
            print(f"[morning] {ticker} skipped (earnings block)")
            results[ticker] = {"final_order": {"action": "hold", "qty": 0}}
            continue

        if _is_same_sector_open(ticker, session_portfolio):
            results[ticker] = {"final_order": {"action": "hold", "qty": 0}}
            continue

        if not _has_volume_confirmation(ticker):
            print(f"[morning] {ticker} skipped (volume filter)")
            results[ticker] = {"final_order": {"action": "hold", "qty": 0}}
            continue

        try:
            print(f"[morning] Analyzing {ticker}...")
            state = run_pipeline(ticker, date, dry_run=True, portfolio=fm_portfolio)
            results[ticker] = state
        except Exception as e:
            print(f"[morning] Pipeline error for {ticker}: {e}")
            results[ticker] = {"error": str(e), "final_order": {"action": "hold", "qty": 0}}
    return results


def _pick_best(results: dict, blocked: set, portfolio: dict, sector_strength: dict):
    """
    Score all BUY candidates and pick the highest-scoring one.
    Score = conviction_rank + sector_bonus + volume_bonus
    Skips: earnings-blocked, same-sector-as-open, volume-filtered.
    Returns (ticker, state, score) or (None, None, 0).
    """
    candidates = []
    for ticker, state in results.items():
        if ticker in blocked:
            continue
        if _is_same_sector_open(ticker, portfolio):
            continue

        order = state.get("final_order", {})
        if order.get("action") != "buy":
            continue

        # For crypto, qty may be 0 (fractional issue) but action=buy — still consider
        is_crypto = "-USD" in ticker

        conviction_str = state.get("trader_decision", {}).get("conviction", "low")
        base_score = float(_CONVICTION_RANK.get(str(conviction_str).lower(), 1))

        # Sector strength bonus
        sector_bonus = get_sector_bonus(ticker, sector_strength) if not is_crypto else 0.0

        # Volume confirmation bonus
        vol_ok = _has_volume_confirmation(ticker)
        if not vol_ok:
            continue   # hard filter, not just a penalty
        vol_bonus = 0.15

        total_score = round(base_score + sector_bonus + vol_bonus, 3)
        print(f"[morning] {ticker}: score={total_score:.2f} (conv={base_score}, sector={sector_bonus:+.2f})")
        candidates.append((total_score, ticker, state))

    if not candidates:
        return None, None, 0

    candidates.sort(key=lambda x: x[0], reverse=True)
    score, ticker, state = candidates[0]
    return ticker, state, score


def _size_position(
    ticker: str,
    state: dict,
    cash: float,
    vix_mult: float,
    regime_mult: float,
    direction: str = "long",
    sector_strength: dict = None,
) -> dict:
    """
    Compute entry price, quantity, TP, SL, and dollar size.
    Handles fractional crypto quantities (stored as float).
    direction: 'long' or 'short'
    sector_strength: used to apply momentum tilt multiplier.
    """
    order  = state.get("final_order", {})
    trader = state.get("trader_decision", {})

    price = get_latest_price(ticker)

    if direction == "short":
        sl_pct = SHORT_SL_PCT  / 100
        tp_pct = SHORT_TP_PCT  / 100
    else:
        sl_pct = float(order.get("stop_loss_pct") or trader.get("stop_loss_pct") or 0.03)
        tp_pct = sl_pct * 2   # 2:1 reward-to-risk

    # Sector momentum tilt
    sector_mult = 1.0
    if sector_strength:
        ticker_sector = TICKER_SECTOR.get(ticker, "")
        top2 = set((sector_strength.get("top_3") or [])[:2])
        bot2 = set((sector_strength.get("bottom_3") or [])[:2])
        if ticker_sector in top2:
            sector_mult = SECTOR_TILT_TOP_MULT
        elif ticker_sector in bot2:
            sector_mult = SECTOR_TILT_BOTTOM_MULT

    pos_frac  = float(order.get("position_size_pct") or 0.25)
    max_usd   = cash * pos_frac * vix_mult * regime_mult * sector_mult
    max_usd   = min(max_usd, cash * 0.25)   # hard cap: never > 25% of cash

    is_crypto = "-USD" in ticker
    if is_crypto:
        qty = round(max_usd / price, 6)
        if qty < 0.0001:
            qty = 0
    else:
        qty = max(1, int(max_usd / price))

    actual_usd = round(qty * price, 2)

    why  = (order.get("final_reasoning") or trader.get("reasoning") or "No reasoning provided.")[:350]
    bull = (state.get("bull_case") or "")[:140]
    bear = (state.get("bear_case") or "")[:140]

    if direction == "short":
        take_profit = round(price * (1 - tp_pct), 2)
        stop_loss   = round(price * (1 + sl_pct), 2)
    else:
        take_profit = round(price * (1 + tp_pct), 2)
        stop_loss   = round(price * (1 - sl_pct), 2)

    return {
        "ticker":            ticker,
        "direction":         direction,
        "current_price":     price,
        "conviction":        state.get("trader_decision", {}).get("conviction", "medium"),
        "why":               why,
        "bull_case":         bull or "See logs.",
        "bear_case":         bear or "See logs.",
        "take_profit":       take_profit,
        "take_profit_pct":   tp_pct * 100,
        "stop_loss":         stop_loss,
        "stop_loss_pct":     sl_pct * 100,
        "position_size_usd": actual_usd,
        "qty":               qty,
        "_sl_pct_raw":       sl_pct,
        "_tp_pct_raw":       tp_pct,
        "_sector_mult":      sector_mult,
        "_full_why":         order.get("final_reasoning") or trader.get("reasoning") or "",
    }


# ---------------------------------------------------------------------------
# Momentum breakout scan (paper day trade signals)
# ---------------------------------------------------------------------------

def _scan_momentum_breakouts(watchlist: list, today: str, sector_strength: dict) -> list:
    """
    Scan watchlist for momentum breakout signals.
    Triggers when price is within MOMENTUM_NEAR_HIGH_PCT% of 52-week high
    AND 3-day avg volume >= DAY_TRADE_VOLUME_RATIO_MIN × 30-day avg.
    Paper-only — no capital allocated.
    """
    signals = []
    one_year_ago = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=370)).strftime("%Y-%m-%d")
    auto_close   = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")

    for ticker in watchlist:
        if ticker.endswith("-USD"):
            continue
        try:
            bars = _yahoo_direct_ohlcv(ticker, one_year_ago, today)
            if len(bars) < 30:
                continue
            highs = [b.get("high", b.get("close", 0)) for b in bars if b.get("high", 0) > 0]
            high_52w = max(highs) if highs else 0
            if high_52w <= 0:
                continue
            current_price = bars[-1].get("close", 0)
            if current_price <= 0:
                continue
            near_high = current_price >= high_52w * (1 - MOMENTUM_NEAR_HIGH_PCT / 100)
            if not near_high:
                continue
            volumes = [b.get("volume", 0) for b in bars if b.get("volume", 0) > 0]
            if len(volumes) < 10:
                continue
            avg_30d   = sum(volumes[-30:]) / min(30, len(volumes))
            avg_3d    = sum(volumes[-3:])  / 3
            vol_ratio = (avg_3d / avg_30d) if avg_30d > 0 else 0
            if vol_ratio < DAY_TRADE_VOLUME_RATIO_MIN:
                print(f"[morning] {ticker} near 52w high — vol ratio {vol_ratio:.2f}x < {DAY_TRADE_VOLUME_RATIO_MIN}x, skipping")
                continue
            target = round(current_price * (1 + MOMENTUM_TARGET_PCT / 100), 2)
            stop   = round(current_price * (1 - MOMENTUM_STOP_PCT   / 100), 2)
            signal = {
                "id":              f"DTS-{today}-{ticker}-momentum",
                "ticker":          ticker,
                "signal_type":     "momentum_breakout",
                "generated_date":  today,
                "entry_price":     round(current_price, 2),
                "target_price":    target,
                "target_pct":      MOMENTUM_TARGET_PCT,
                "stop_price":      stop,
                "stop_pct":        MOMENTUM_STOP_PCT,
                "status":          "open",
                "exit_price":      None,
                "exit_date":       None,
                "pnl_pct":         None,
                "outcome":         None,
                "auto_close_date": auto_close,
                "rationale":       f"Near 52w high (${high_52w:.2f}) — vol ratio {vol_ratio:.1f}x",
            }
            add_day_trade_signal(signal)
            send_group_trade_signal(signal)
            signals.append(signal)
            print(f"[morning] Momentum breakout: {ticker} @ ${current_price:.2f}  52wH=${high_52w:.2f}  vol={vol_ratio:.1f}x")
        except Exception as e:
            print(f"[morning] Momentum scan error {ticker}: {e}")

    return signals


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    print(f"\n[morning] ========== Morning Session {today} ==========")

    portfolio = get_portfolio()

    # ── Start new session if none active ───────────────────────────────────
    if not portfolio["session"]["active"]:
        print("[morning] No active session — starting a new 10-day session.")
        portfolio = start_session()

    if not is_session_active():
        _done_days = portfolio["session"].get("total_days", 22)
        broadcast_message(f"🏁 <b>Paper trading session complete!</b>\n\nAll {_done_days} days are done. Check the dashboard.")
        return

    session_day = get_session_day()
    total_days  = portfolio["session"]["total_days"]
    equity      = portfolio.get("equity", portfolio["initial_capital"])
    cash        = portfolio.get("cash",   portfolio["initial_capital"])
    open_count  = len(portfolio.get("positions", {}))

    print(f"[morning] Day {session_day}/{total_days}  |  Equity: ${equity:,.2f}  |  Cash: ${cash:,.2f}  |  Open: {open_count}")

    # ── SPY benchmark anchor ───────────────────────────────────────────────
    if session_day == 1 and not portfolio.get("stats", {}).get("spy_start_price"):
        try:
            set_spy_start_price(get_latest_price("SPY"))
        except Exception as e:
            print(f"[morning] SPY anchor error: {e}")

    # ── Circuit breaker ────────────────────────────────────────────────────
    halt, halt_reason = check_circuit_breaker(equity)
    if halt:
        broadcast_message(
            f"🚨 <b>CIRCUIT BREAKER — Day {session_day}/{total_days}</b>\n\n"
            f"{halt_reason}\n\n<b>Trading halted to protect capital.</b>"
        )
        record_equity(equity)
        return

    # ── Macro event block ──────────────────────────────────────────────────
    event_blocked, event_reason = is_event_blocked(today)
    if event_blocked:
        broadcast_message(
            f"📅 <b>No Trade — Macro Event — Day {session_day}/{total_days}</b>\n\n"
            f"🚫 {event_reason}\nStaying in cash."
        )
        record_equity(equity)
        return

    # ── VIX regime ────────────────────────────────────────────────────────
    vix_multiplier, vix_label = get_vix_multiplier()
    print(f"[morning] VIX: {vix_label}  sizing={vix_multiplier}x")

    if vix_multiplier == 0.0:
        broadcast_message(
            f"🚨 <b>No Trade — VIX Extreme — Day {session_day}/{total_days}</b>\n\n"
            f"VIX: {vix_label}\nStaying in cash.\n\nSession equity: <b>${equity:,.2f}</b>"
        )
        record_equity(equity)
        return

    # ── VIX rate-of-change filter ─────────────────────────────────────────
    vix_roc, vix_roc_label = get_vix_roc()
    if vix_roc is not None and vix_roc > VIX_ROC_THRESHOLD:
        print(f"[morning] VIX RoC {vix_roc:.1f}% > {VIX_ROC_THRESHOLD}% — additional 0.5× size cut")
        vix_multiplier = round(vix_multiplier * 0.5, 2)
        vix_label      = f"{vix_label} | RoC {vix_roc:+.1f}% 🔺"

    # ── Market regime overlay (SPY vs 200d MA) ────────────────────────────
    spy_trend = get_market_trend("SPY")
    bearish   = spy_trend.get("above_ma200") is False

    if bearish and ALLOW_SHORT_SELLING:
        trade_direction = "short"
        regime_mult     = 1.0   # full size — we're shorting with the trend
        regime_label    = "BEARISH (SPY < 200d MA) — seeking SHORT candidates"
    elif bearish:
        trade_direction = "long"
        regime_mult     = BEARISH_REGIME_MULTIPLIER
        regime_label    = "BEARISH (SPY < 200d MA) — halving size"
    else:
        trade_direction = "long"
        regime_mult     = 1.0
        regime_label    = "BULLISH"
    print(f"[morning] Market regime: {regime_label}  direction={trade_direction}")

    # ── HYG credit spread check ───────────────────────────────────────────
    hyg_risk_off, _hyg_pct, hyg_label = get_hyg_signal()
    print(f"[morning] HYG: {hyg_label}")
    if hyg_risk_off and vix_multiplier > 0.5:
        print(f"[morning] HYG risk-off — capping sizing at 0.5×")
        vix_multiplier = min(vix_multiplier, 0.5)
        vix_label      = f"{vix_label} | {hyg_label}"

    # ── Concurrent position limit ──────────────────────────────────────────
    if open_count >= MAX_CONCURRENT_POSITIONS:
        broadcast_message(
            f"⏸ <b>No New Trade — Day {session_day}/{total_days}</b>\n\n"
            f"Already at max concurrent positions ({open_count}/{MAX_CONCURRENT_POSITIONS}).\n"
            f"Waiting for existing positions to resolve before adding new exposure."
        )
        record_equity(equity)
        return

    # ── Portfolio heat check ───────────────────────────────────────────────
    heat = _portfolio_heat(portfolio)
    if heat >= MAX_PORTFOLIO_HEAT:
        broadcast_message(
            f"🌡 <b>No New Trade — Day {session_day}/{total_days}</b>\n\n"
            f"Portfolio heat: {heat*100:.0f}% deployed (max {MAX_PORTFOLIO_HEAT*100:.0f}%).\n"
            f"Keeping dry powder. No new positions today."
        )
        record_equity(equity)
        return

    # ── Earnings check ────────────────────────────────────────────────────
    earnings_blocked = set()
    for t in WATCHLIST:
        e = has_earnings_soon(t, days=5)
        if e["has_earnings"]:
            earnings_blocked.add(t)
            print(f"[morning] {t} blocked — upcoming earnings {e['date']}")
        elif had_earnings_recently(t, days=2):
            earnings_blocked.add(t)
            print(f"[morning] {t} blocked — post-earnings cooldown (reported in last 2 days)")

    # ── Sector strength (runs before pipeline to inform ranking) ──────────
    print("[morning] Fetching sector strength...")
    top3    = ""
    bottom3 = ""
    try:
        sector_strength = get_sector_strength()
        top3    = ", ".join(sector_strength.get("top_3", []))
        bottom3 = ", ".join(sector_strength.get("bottom_3", []))
        print(f"[morning] Sector leaders: {top3}  |  Laggards: {bottom3}")
    except Exception as e:
        print(f"[morning] Sector strength error: {e}")
        sector_strength = {"ranking": [], "top_3": [], "bottom_3": [], "sectors": {}}

    # ── Momentum breakout scan (paper day trade signals) ──────────────────
    _scan_momentum_breakouts(WATCHLIST, today, sector_strength)

    # ── Notify user: analysis starting ────────────────────────────────────
    dir_note = " 🔻 Shorting mode" if trade_direction == "short" else ""
    broadcast_message(
        f"🔍 <b>Day {session_day}/{total_days}</b>{dir_note} — Analysing {len(WATCHLIST)} tickers "
        f"across {len(SECTOR_MAP)} sectors...\n"
        f"VIX: {vix_label}  |  Regime: {regime_label}  |  {vix_roc_label}\n"
        f"Credit: {hyg_label}\n"
        f"Sector leaders: {top3 or 'N/A'}\n"
        f"<i>Back in ~15–20 min with the best trade.</i>"
    )

    # ── Run full AI pipeline ───────────────────────────────────────────────
    results = _analyze_all(today, portfolio, earnings_blocked)

    # ── Pick best candidate ────────────────────────────────────────────────
    ticker, state, score = _pick_best(results, earnings_blocked, portfolio, sector_strength)

    if ticker is None:
        blocked_note = f"\nEarnings-blocked: {', '.join(earnings_blocked)}" if earnings_blocked else ""
        broadcast_message(
            f"📭 <b>No Trade Today — Day {session_day}/{total_days}</b>\n\n"
            f"No valid BUY signals after filters (volume, sector, earnings).{blocked_note}\n\n"
            f"Session equity: <b>${equity:,.2f}</b>"
        )
        record_equity(equity)
        return

    # ── Portfolio beta cap ────────────────────────────────────────────────
    if ticker and "-USD" not in ticker:
        pos_frac      = float(state.get("final_order", {}).get("position_size_pct") or 0.25)
        estimated_usd = cash * pos_frac * vix_multiplier * regime_mult
        port_beta     = _portfolio_beta(portfolio, ticker, estimated_usd)
        print(f"[morning] Portfolio beta (incl {ticker}): {port_beta:.2f}  cap={MAX_PORTFOLIO_BETA}")
        if port_beta > MAX_PORTFOLIO_BETA:
            broadcast_message(
                f"📊 <b>Beta Cap — Day {session_day}/{total_days}</b>\n\n"
                f"Adding {ticker} would push portfolio β to {port_beta:.2f} "
                f"(max {MAX_PORTFOLIO_BETA}).\n"
                f"Staying in cash to maintain diversification.\n\n"
                f"Session equity: <b>${equity:,.2f}</b>"
            )
            record_equity(equity)
            return

    # ── Size the position ──────────────────────────────────────────────────
    summary = _size_position(ticker, state, cash, vix_multiplier, regime_mult,
                             direction=trade_direction, sector_strength=sector_strength)
    summary["session_day"]  = session_day
    summary["total_days"]   = total_days
    summary["vix_label"]    = vix_label
    summary["sector"]       = TICKER_SECTOR.get(ticker, "")
    summary["sector_rank"]  = sector_strength.get("ranking", []).index(summary["sector"]) + 1 \
                              if summary["sector"] in sector_strength.get("ranking", []) else "N/A"

    if summary["qty"] <= 0:
        broadcast_message(
            f"📭 <b>No Trade — {ticker} — Day {session_day}/{total_days}</b>\n\n"
            f"Ticker selected but position size rounds to 0 (price too high for available capital).\n"
            f"Cash: ${cash:,.2f}  |  {ticker} @ ${summary['current_price']:,.2f}"
        )
        record_equity(equity)
        return

    print(f"[morning] Best: {ticker}  score={score:.2f}  sector={summary['sector']}  qty={summary['qty']}")

    # ── Log order as pending before sending for approval ──────────────────
    add_open_order(ticker, summary["qty"], summary["current_price"], "BUY")

    # ── Send Telegram approval card (private) + group informational card ──
    send_approval_request(summary)
    send_group_trade_signal(summary)
    print(f"[morning] Approval sent. Polling {APPROVAL_TIMEOUT_SECONDS // 60} min...")

    response = poll_for_response(timeout_seconds=APPROVAL_TIMEOUT_SECONDS)
    print(f"[morning] Response: {response}")

    if response == "approved":
        # Re-fetch price at execution time — not the pre-market analysis-time quote
        try:
            exec_price = get_latest_price(ticker)
        except Exception:
            exec_price = summary["current_price"]
        if summary.get("direction") == "short":
            exec_stop_loss   = round(exec_price * (1 + summary["_sl_pct_raw"]), 2)
            exec_take_profit = round(exec_price * (1 - summary["_tp_pct_raw"]), 2)
        else:
            exec_stop_loss   = round(exec_price * (1 - summary["_sl_pct_raw"]), 2)
            exec_take_profit = round(exec_price * (1 + summary["_tp_pct_raw"]), 2)
        price_delta_pct  = round((exec_price - summary["current_price"]) / summary["current_price"] * 100, 2) \
                           if summary["current_price"] > 0 else 0.0
        print(f"[morning] Analysis: ${summary['current_price']:.2f} → Exec: ${exec_price:.2f} ({price_delta_pct:+.2f}%)")

        update_open_order(ticker, "executed")
        open_position(
            ticker          = ticker,
            qty             = summary["qty"],
            entry_price     = exec_price,
            stop_loss_pct   = summary["_sl_pct_raw"],
            take_profit_pct = summary["_tp_pct_raw"],
            journal_note    = summary["_full_why"][:500],
            direction       = summary.get("direction", "long"),
        )

        # Capture which agents were aligned on this trade
        agent_signals = {
            "fundamental":       state.get("fundamental_analysis", {}).get("recommendation", ""),
            "technical":         state.get("technical_analysis",   {}).get("signal", ""),
            "sentiment":         state.get("sentiment_analysis",   {}).get("sentiment", ""),
            "trader_conviction": state.get("trader_decision",      {}).get("conviction", ""),
            "risk_approved":     state.get("risk_assessment",      {}).get("approved", None),
        }

        add_journal_entry({
            "date":            today,
            "day":             session_day,
            "ticker":          ticker,
            "action":          "BUY",
            "analysis_price":  summary["current_price"],
            "entry_price":     exec_price,
            "price_delta_pct": price_delta_pct,
            "qty":             summary["qty"],
            "conviction":      summary["conviction"],
            "score":           score,
            "sector":          summary["sector"],
            "sector_rank":     summary["sector_rank"],
            "stop_loss":       exec_stop_loss,
            "take_profit":     exec_take_profit,
            "vix_label":       vix_label,
            "regime":          regime_label,
            "rationale":       summary["_full_why"],
            "bull_case":       summary["bull_case"],
            "bear_case":       summary["bear_case"],
            "agent_signals":   agent_signals,
        })
        is_short = summary.get("direction") == "short"
        dir_tag  = "SHORT 🔻" if is_short else "BUY"
        if is_short:
            partial_level = round(exec_price * (1 - summary["_sl_pct_raw"]), 2)
        else:
            partial_level = round(exec_price * (1 + summary["_sl_pct_raw"]), 2)
        broadcast_message(
            f"✅ <b>Trade Executed — {ticker} {dir_tag}</b>\n\n"
            f"{dir_tag} {summary['qty']} @ ${exec_price:.2f}\n"
            f"Deployed: ${round(exec_price * summary['qty'], 2):.2f}  |  "
            f"Sector: {summary['sector']} (rank #{summary['sector_rank']})\n\n"
            f"TP: ${exec_take_profit:.2f}  |  SL: ${exec_stop_loss:.2f}\n"
            f"Partial profit triggers at: ${partial_level:.2f} (1:1 R/R)\n\n"
            f"<i>EOD check at 4:15 PM ET.</i>"
        )
        record_equity(equity)

    elif response == "rejected":
        update_open_order(ticker, "rejected")
        broadcast_message(f"⏭ <b>Trade Skipped — Day {session_day}/{total_days}</b>\n\nNo position opened. See you tomorrow!")
        record_equity(equity)

    else:
        update_open_order(ticker, "expired")
        broadcast_message(f"⏰ <b>60-min window expired — Day {session_day}/{total_days}</b>\n\nTrade skipped.")
        record_equity(equity)

    print(f"[morning] Day {session_day} complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err = str(e)
        # Send Telegram alert for critical failures so they're not silent
        try:
            from tools.telegram_bot import broadcast_message as _bcast
            _bcast(
                f"🚨 <b>Morning Session FAILED</b>\n\n"
                f"<code>{err[:1000]}</code>\n\n"
                f"Check: https://github.com/outtcom/ai-paper-trading/actions"
            )
        except Exception:
            pass
        raise
