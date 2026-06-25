"""
Agent accuracy tracker — Phase 1 IC (Information Coefficient) monitoring.

Runs at EOD after each day's closes are recorded. Reads from portfolio.json:
  - journal entries (action="BUY") → per-agent signals at entry
  - trade_history → actual outcomes (pnl, pnl_pct)

Persists cumulative stats in portfolio.json under "agent_tracker".
Uses _counted_trades to avoid double-counting across daily runs.

IC interpretation:
  win_rate > 60%  → strong predictive signal
  win_rate > 55%  → useful signal
  win_rate ~50%   → noise (random)
  win_rate < 45%  → contra-indicator (agent is consistently wrong)
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

_PORTFOLIO_FILE = Path(__file__).parent.parent / "docs" / "portfolio.json"


# ──────────────────────────────────────────────────────────────────────────────
# Signal parsers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_fundamental(sig: str) -> str:
    s = sig.upper()
    if any(k in s for k in ("BUY", "BULLISH", "OUTPERFORM", "STRONG BUY", "OVERWEIGHT")):
        return "bullish"
    if any(k in s for k in ("SELL", "BEARISH", "UNDERPERFORM", "STRONG SELL", "UNDERWEIGHT")):
        return "bearish"
    return "neutral"


def _parse_directional(sig: str) -> str:
    s = sig.lower()
    if any(k in s for k in ("bullish", "buy", "strong")):
        return "bullish"
    if any(k in s for k in ("bearish", "sell")):
        return "bearish"
    return "neutral"


def _parse_sentiment(sig: str) -> str:
    s = sig.lower()
    if any(k in s for k in ("positive", "bullish", "optimistic")):
        return "bullish"
    if any(k in s for k in ("negative", "bearish", "pessimistic")):
        return "bearish"
    return "neutral"


# ──────────────────────────────────────────────────────────────────────────────
# Tally helpers
# ──────────────────────────────────────────────────────────────────────────────

def _empty_bucket() -> dict:
    return {"n": 0, "wins": 0, "pnl_sum": 0.0}


def _tally(tracker: dict, group: str, key: str, win: bool, pnl_pct: float) -> None:
    by_agent = tracker.setdefault("by_agent", {})
    bucket   = by_agent.setdefault(group, {}).setdefault(key, _empty_bucket())
    bucket["n"]       += 1
    bucket["wins"]    += 1 if win else 0
    bucket["pnl_sum"] += round(pnl_pct, 4)


# ──────────────────────────────────────────────────────────────────────────────
# Core update
# ──────────────────────────────────────────────────────────────────────────────

def update_portfolio_tracker(portfolio: dict) -> dict:
    """
    Scan trade_history for closed trades not yet counted.
    Link each to its BUY journal entry to get agent signals.
    Accumulate per-agent win rates and avg P&L in portfolio["agent_tracker"].
    Returns the updated portfolio dict (caller must save it).
    """
    tracker        = portfolio.setdefault("agent_tracker", {})
    already_seen   = set(tracker.get("_counted_trades", []))
    new_counted    = []

    # Build lookup: (ticker, opened_date) → agent_signals
    signal_map: dict = {}
    for entry in portfolio.get("journal", []):
        if entry.get("action") == "BUY":
            key = (entry.get("ticker", ""), entry.get("date", ""))
            if key[0] and key[1]:
                signal_map[key] = entry.get("agent_signals", {})

    processed = 0
    for trade in portfolio.get("trade_history", []):
        ticker      = trade.get("ticker", "")
        opened_date = trade.get("opened_date", "")
        if not ticker or not opened_date:
            continue
        if not trade.get("closed_date"):       # still open
            continue

        trade_id = f"{ticker}_{opened_date}"
        if trade_id in already_seen:
            continue                            # already counted in a prior run

        signals  = signal_map.get((ticker, opened_date), {})
        win      = (trade.get("pnl", 0) or 0) > 0
        pnl_pct  = float(trade.get("pnl_pct", 0) or 0)

        # ── Fundamental ──────────────────────────────────────────────────────
        fund_dir = _parse_fundamental(str(signals.get("fundamental", "")))
        _tally(tracker, "fundamental", fund_dir, win, pnl_pct)

        # ── Technical ────────────────────────────────────────────────────────
        tech_dir = _parse_directional(str(signals.get("technical", "")))
        _tally(tracker, "technical", tech_dir, win, pnl_pct)

        # ── Sentiment ────────────────────────────────────────────────────────
        sent_dir = _parse_sentiment(str(signals.get("sentiment", "")))
        _tally(tracker, "sentiment", sent_dir, win, pnl_pct)

        # ── Trader conviction ────────────────────────────────────────────────
        conviction = str(signals.get("trader_conviction", "")).lower()
        if conviction in ("high", "medium", "low"):
            _tally(tracker, "conviction", conviction, win, pnl_pct)

        # ── Risk gate ────────────────────────────────────────────────────────
        risk_approved = signals.get("risk_approved")
        if risk_approved is not None:
            risk_key = "approved" if risk_approved else "overridden"
            _tally(tracker, "risk", risk_key, win, pnl_pct)

        new_counted.append(trade_id)
        processed += 1

    if processed:
        counted_list = list(already_seen) + new_counted
        tracker["_counted_trades"] = counted_list
        tracker["last_updated"]    = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tracker["total_closed"]    = len(counted_list)
        print(f"[agent_tracker] Processed {processed} new closed trade(s). "
              f"Total tracked: {len(counted_list)}")
    else:
        print("[agent_tracker] No new closed trades to process.")

    return portfolio


# ──────────────────────────────────────────────────────────────────────────────
# Formatting
# ──────────────────────────────────────────────────────────────────────────────

def _win_rate_str(bucket: dict) -> str:
    n = bucket.get("n", 0)
    if n == 0:
        return "—"
    wr  = bucket["wins"] / n * 100
    avg = bucket["pnl_sum"] / n
    return f"{wr:.0f}% win ({n}tr, avg {avg:+.1f}%)"


def format_ic_report(portfolio: dict) -> str:
    """Return a Telegram-formatted IC summary. Empty string if no data yet."""
    tracker = portfolio.get("agent_tracker", {})
    total   = tracker.get("total_closed", 0)
    if total == 0:
        return ""

    by = tracker.get("by_agent", {})

    def row(group: str, key: str, label: str) -> str:
        b = by.get(group, {}).get(key, {})
        return f"  {label}: {_win_rate_str(b)}"

    lines = [
        f"🧠 <b>Agent IC Tracker</b> — {total} closed trades\n",
        "<b>Fundamental (bullish calls):</b>",
        row("fundamental", "bullish", "Bullish"),
        row("fundamental", "neutral", "Neutral"),
        "",
        "<b>Technical (bullish signal):</b>",
        row("technical",   "bullish", "Bullish"),
        row("technical",   "neutral", "Neutral"),
        "",
        "<b>Sentiment (positive):</b>",
        row("sentiment",   "bullish", "Positive"),
        row("sentiment",   "neutral", "Neutral"),
        "",
        "<b>Trader Conviction:</b>",
        row("conviction",  "high",   "High    "),
        row("conviction",  "medium", "Medium  "),
        row("conviction",  "low",    "Low     "),
        "",
        "<b>Risk Gate:</b>",
        row("risk", "approved",   "Approved "),
        row("risk", "overridden", "Overridden"),
    ]
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# File I/O  (used when called standalone — eod_session.py passes the dict)
# ──────────────────────────────────────────────────────────────────────────────

def run_from_file() -> None:
    """Standalone entry point — reads and writes portfolio.json directly."""
    if not _PORTFOLIO_FILE.exists():
        print("[agent_tracker] portfolio.json not found.")
        return
    with open(_PORTFOLIO_FILE) as f:
        portfolio = json.load(f)

    portfolio = update_portfolio_tracker(portfolio)

    with open(_PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)

    report = format_ic_report(portfolio)
    if report:
        print("\n" + report)


if __name__ == "__main__":
    run_from_file()
