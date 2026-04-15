"""
Morning session entry point.
Triggered by GitHub Actions at 7:30 AM ET on weekdays.

Flow:
  1.  Start/check the 10-day session
  2.  Circuit breaker — halt if peak drawdown > 15% or daily loss > 3%
  3.  FOMC / CPI / NFP auto-block — no trades on macro event days
  4.  VIX check — EXTREME → skip. Apply VIX sizing multiplier.
  5.  Market regime overlay — bearish (SPY < 200d MA) → halve size further
  6.  Concurrent position limit — skip if already at MAX_CONCURRENT_POSITIONS
  7.  Portfolio heat check — skip if >75% capital already deployed
  8.  Earnings check — block tickers with earnings within 3 days
  9.  Sector strength ranking — fetch sector ETF momentum before analysis
  10. Run 7-agent pipeline on all watchlist tickers (stocks + crypto)
  11. Volume confirmation filter — discard low-volume BUY signals
  12. Rank candidates: conviction + sector bonus → pick best
  13. Send Telegram approval card; poll 60 min
  14. If approved: execute paper trade, log journal entry

Usage:
  python morning_session.py
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    WATCHLIST, APPROVAL_TIMEOUT_SECONDS,
    MAX_CONCURRENT_POSITIONS, MAX_PORTFOLIO_HEAT, MIN_VOLUME_RATIO,
    BEARISH_REGIME_MULTIPLIER, TICKER_SECTOR,
)
from orchestrator import run_pipeline
from tools.market_data import get_latest_price, get_ohlcv
from tools.market_regime import get_vix_multiplier, get_market_trend, has_earnings_soon, is_event_blocked
from tools.sector_analysis import get_sector_strength, get_sector_bonus, format_sector_heatmap
from tools.session_manager import (
    add_journal_entry,
    check_circuit_breaker,
    get_portfolio,
    get_session_day,
    is_session_active,
    open_position,
    record_equity,
    set_spy_start_price,
    start_session,
)
from tools.telegram_bot import poll_for_response, send_approval_request, send_message

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
        end   = datetime.today().strftime("%Y-%m-%d")
        start = (datetime.today() - timedelta(days=35)).strftime("%Y-%m-%d")
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


def _size_position(ticker: str, state: dict, cash: float, vix_mult: float, regime_mult: float) -> dict:
    """
    Compute entry price, quantity, TP, SL, and dollar size.
    Handles fractional crypto quantities (stored as float).
    """
    order  = state.get("final_order", {})
    trader = state.get("trader_decision", {})

    price     = get_latest_price(ticker)
    sl_pct    = float(order.get("stop_loss_pct") or trader.get("stop_loss_pct") or 0.03)
    tp_pct    = sl_pct * 2            # 2:1 reward-to-risk

    pos_frac  = float(order.get("position_size_pct") or 0.25)
    max_usd   = cash * pos_frac * vix_mult * regime_mult
    max_usd   = min(max_usd, cash * 0.25)   # hard cap: never > 25% of cash

    is_crypto = "-USD" in ticker
    if is_crypto:
        qty = round(max_usd / price, 6)      # fractional units
        if qty < 0.0001:
            qty = 0
    else:
        qty = max(1, int(max_usd / price))   # whole shares

    actual_usd = round(qty * price, 2)

    why  = (order.get("final_reasoning") or trader.get("reasoning") or "No reasoning provided.")[:350]
    bull = (state.get("bull_case") or "")[:140]
    bear = (state.get("bear_case") or "")[:140]

    return {
        "ticker":           ticker,
        "current_price":    price,
        "conviction":       state.get("trader_decision", {}).get("conviction", "medium"),
        "why":              why,
        "bull_case":        bull or "See logs.",
        "bear_case":        bear or "See logs.",
        "take_profit":      round(price * (1 + tp_pct), 4),
        "take_profit_pct":  tp_pct * 100,
        "stop_loss":        round(price * (1 - sl_pct), 4),
        "stop_loss_pct":    sl_pct * 100,
        "position_size_usd": actual_usd,
        "qty":              qty,
        "_sl_pct_raw":      sl_pct,
        "_tp_pct_raw":      tp_pct,
        "_full_why":        order.get("final_reasoning") or trader.get("reasoning") or "",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    today = datetime.today().strftime("%Y-%m-%d")
    print(f"\n[morning] ========== Morning Session {today} ==========")

    portfolio = get_portfolio()

    # ── Start new session if none active ───────────────────────────────────
    if not portfolio["session"]["active"]:
        print("[morning] No active session — starting a new 10-day session.")
        portfolio = start_session()

    if not is_session_active():
        send_message("🏁 <b>Paper trading session complete!</b>\n\nAll 10 days are done. Check the dashboard.")
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
        send_message(
            f"🚨 <b>CIRCUIT BREAKER — Day {session_day}/{total_days}</b>\n\n"
            f"{halt_reason}\n\n<b>Trading halted to protect capital.</b>"
        )
        record_equity(equity)
        return

    # ── Macro event block ──────────────────────────────────────────────────
    event_blocked, event_reason = is_event_blocked(today)
    if event_blocked:
        send_message(
            f"📅 <b>No Trade — Macro Event — Day {session_day}/{total_days}</b>\n\n"
            f"🚫 {event_reason}\nStaying in cash."
        )
        record_equity(equity)
        return

    # ── VIX regime ────────────────────────────────────────────────────────
    vix_multiplier, vix_label = get_vix_multiplier()
    print(f"[morning] VIX: {vix_label}  sizing={vix_multiplier}x")

    if vix_multiplier == 0.0:
        send_message(
            f"🚨 <b>No Trade — VIX Extreme — Day {session_day}/{total_days}</b>\n\n"
            f"VIX: {vix_label}\nStaying in cash.\n\nSession equity: <b>${equity:,.2f}</b>"
        )
        record_equity(equity)
        return

    # ── Market regime overlay (SPY vs 200d MA) ────────────────────────────
    spy_trend    = get_market_trend("SPY")
    regime_mult  = BEARISH_REGIME_MULTIPLIER if spy_trend.get("above_ma200") is False else 1.0
    regime_label = "BEARISH (SPY < 200d MA) — halving size" if regime_mult < 1.0 else "BULLISH"
    print(f"[morning] Market regime: {regime_label}")

    # ── Concurrent position limit ──────────────────────────────────────────
    if open_count >= MAX_CONCURRENT_POSITIONS:
        send_message(
            f"⏸ <b>No New Trade — Day {session_day}/{total_days}</b>\n\n"
            f"Already at max concurrent positions ({open_count}/{MAX_CONCURRENT_POSITIONS}).\n"
            f"Waiting for existing positions to resolve before adding new exposure."
        )
        record_equity(equity)
        return

    # ── Portfolio heat check ───────────────────────────────────────────────
    heat = _portfolio_heat(portfolio)
    if heat >= MAX_PORTFOLIO_HEAT:
        send_message(
            f"🌡 <b>No New Trade — Day {session_day}/{total_days}</b>\n\n"
            f"Portfolio heat: {heat*100:.0f}% deployed (max {MAX_PORTFOLIO_HEAT*100:.0f}%).\n"
            f"Keeping dry powder. No new positions today."
        )
        record_equity(equity)
        return

    # ── Earnings check ────────────────────────────────────────────────────
    earnings_blocked = set()
    for t in WATCHLIST:
        e = has_earnings_soon(t, days=3)
        if e["has_earnings"]:
            earnings_blocked.add(t)
            print(f"[morning] {t} blocked — earnings {e['date']}")

    # ── Sector strength (runs before pipeline to inform ranking) ──────────
    print("[morning] Fetching sector strength...")
    try:
        sector_strength = get_sector_strength()
        top3    = ", ".join(sector_strength.get("top_3", []))
        bottom3 = ", ".join(sector_strength.get("bottom_3", []))
        print(f"[morning] Sector leaders: {top3}  |  Laggards: {bottom3}")
    except Exception as e:
        print(f"[morning] Sector strength error: {e}")
        sector_strength = {"ranking": [], "top_3": [], "bottom_3": [], "sectors": {}}

    # ── Notify user: analysis starting ────────────────────────────────────
    send_message(
        f"🔍 <b>Day {session_day}/{total_days}</b> — Analysing {len(WATCHLIST)} tickers "
        f"across {len([s for s in sector_strength.get('top_3',[])])} sectors...\n"
        f"VIX: {vix_label}  |  Regime: {regime_label}\n"
        f"Sector leaders: {top3}\n"
        f"<i>Back in ~15–20 min with the best trade.</i>"
    )

    # ── Run full AI pipeline ───────────────────────────────────────────────
    results = _analyze_all(today, portfolio, earnings_blocked)

    # ── Pick best candidate ────────────────────────────────────────────────
    ticker, state, score = _pick_best(results, earnings_blocked, portfolio, sector_strength)

    if ticker is None:
        blocked_note = f"\nEarnings-blocked: {', '.join(earnings_blocked)}" if earnings_blocked else ""
        send_message(
            f"📭 <b>No Trade Today — Day {session_day}/{total_days}</b>\n\n"
            f"No valid BUY signals after filters (volume, sector, earnings).{blocked_note}\n\n"
            f"Session equity: <b>${equity:,.2f}</b>"
        )
        record_equity(equity)
        return

    # ── Size the position ──────────────────────────────────────────────────
    summary = _size_position(ticker, state, cash, vix_multiplier, regime_mult)
    summary["session_day"]  = session_day
    summary["total_days"]   = total_days
    summary["vix_label"]    = vix_label
    summary["sector"]       = TICKER_SECTOR.get(ticker, "")
    summary["sector_rank"]  = sector_strength.get("ranking", []).index(summary["sector"]) + 1 \
                              if summary["sector"] in sector_strength.get("ranking", []) else "N/A"

    if summary["qty"] <= 0:
        send_message(
            f"📭 <b>No Trade — {ticker} — Day {session_day}/{total_days}</b>\n\n"
            f"Ticker selected but position size rounds to 0 (price too high for available capital).\n"
            f"Cash: ${cash:,.2f}  |  {ticker} @ ${summary['current_price']:,.2f}"
        )
        record_equity(equity)
        return

    print(f"[morning] Best: {ticker}  score={score:.2f}  sector={summary['sector']}  qty={summary['qty']}")

    # ── Send Telegram approval card ────────────────────────────────────────
    send_approval_request(summary)
    print(f"[morning] Approval sent. Polling {APPROVAL_TIMEOUT_SECONDS // 60} min...")

    response = poll_for_response(timeout_seconds=APPROVAL_TIMEOUT_SECONDS)
    print(f"[morning] Response: {response}")

    if response == "approved":
        open_position(
            ticker         = ticker,
            qty            = summary["qty"],
            entry_price    = summary["current_price"],
            stop_loss_pct  = summary["_sl_pct_raw"],
            take_profit_pct= summary["_tp_pct_raw"],
            journal_note   = summary["_full_why"][:500],
        )
        add_journal_entry({
            "date":        today,
            "day":         session_day,
            "ticker":      ticker,
            "action":      "BUY",
            "entry_price": summary["current_price"],
            "qty":         summary["qty"],
            "conviction":  summary["conviction"],
            "score":       score,
            "sector":      summary["sector"],
            "sector_rank": summary["sector_rank"],
            "stop_loss":   summary["stop_loss"],
            "take_profit": summary["take_profit"],
            "vix_label":   vix_label,
            "regime":      regime_label,
            "rationale":   summary["_full_why"],
            "bull_case":   summary["bull_case"],
            "bear_case":   summary["bear_case"],
        })
        send_message(
            f"✅ <b>Trade Executed — {ticker}</b>\n\n"
            f"BUY {summary['qty']} @ ${summary['current_price']:.2f}\n"
            f"Deployed: ${summary['position_size_usd']:.2f}  |  "
            f"Sector: {summary['sector']} (rank #{summary['sector_rank']})\n\n"
            f"TP: ${summary['take_profit']:.2f}  |  SL: ${summary['stop_loss']:.2f}\n"
            f"Partial profit triggers at: "
            f"${round(summary['current_price'] * (1 + summary['_sl_pct_raw']), 2):.2f} (1:1 R/R)\n\n"
            f"<i>EOD check at 4:15 PM ET.</i>"
        )
        record_equity(equity)

    elif response == "rejected":
        send_message(f"⏭ <b>Trade Skipped — Day {session_day}/{total_days}</b>\n\nNo position opened. See you tomorrow!")
        record_equity(equity)

    else:
        send_message(f"⏰ <b>60-min window expired — Day {session_day}/{total_days}</b>\n\nTrade skipped.")
        record_equity(equity)

    print(f"[morning] Day {session_day} complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err = str(e)
        # Send Telegram alert for critical failures so they're not silent
        try:
            from tools.telegram_bot import send_message
            send_message(
                f"🚨 <b>Morning Session FAILED</b>\n\n"
                f"<code>{err[:1000]}</code>\n\n"
                f"Check: https://github.com/outtcom/ai-paper-trading/actions"
            )
        except Exception:
            pass
        raise
