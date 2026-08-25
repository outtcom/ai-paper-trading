"""
System Health Report
====================
Runs twice daily via GitHub Actions:
  - Pre-flight  (~11 AM ET, before/around market open)
  - EOD recap   (~6:30 PM ET, after market close)

Sends a traffic-light summary to PRIVATE Telegram ONLY. Never goes to the group.

Traffic lights:
  🟢 GREEN  — all checks pass, no action needed
  🟡 YELLOW — soft warning, monitor but system is running
  🔴 RED    — immediate attention required

Mode (preflight vs recap) is inferred from ET time of day (before/after noon).
Override with --mode preflight or --mode recap for manual testing.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

_SYSTEM_DIR    = Path(__file__).parent
PORTFOLIO_FILE = _SYSTEM_DIR / "docs" / "portfolio.json"
STATE_DIR      = _SYSTEM_DIR / ".tmp" / "state"

sys.path.insert(0, str(_SYSTEM_DIR))

from tools.telegram_bot import send_private_only
from config import INITIAL_CAPITAL, SESSION_DAYS


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _et_now() -> datetime:
    return datetime.now(ZoneInfo("America/New_York"))


def _load_portfolio() -> dict:
    if not PORTFOLIO_FILE.exists():
        return {}
    with open(PORTFOLIO_FILE) as f:
        return json.load(f)


def _load_audit_log(date_str: str) -> dict:
    path = STATE_DIR / date_str / "decision_audit.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


# ──────────────────────────────────────────────────────────────────────────────
# Checks — each returns (status, message)
# status: "ok" | "warn" | "error"
# ──────────────────────────────────────────────────────────────────────────────

def check_staleness(portfolio: dict) -> tuple:
    last_str = portfolio.get("session", {}).get("last_updated")
    if not last_str:
        return "error", "No last_updated timestamp in portfolio.json"
    try:
        last_dt = datetime.fromisoformat(last_str)
        age_h = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
    except Exception:
        return "warn", "Could not parse last_updated timestamp"

    now_et = _et_now()
    weekday = now_et.weekday()  # 0=Mon
    # Monday allows up to 90h gap (covers the full weekend from Friday EOD)
    threshold = 90 if weekday == 0 else 36
    if age_h > threshold:
        return "error", f"Portfolio last updated {age_h:.0f}h ago — workflow may be silently failing"
    if age_h > 24 and weekday not in (0,):
        return "warn", f"Portfolio last updated {age_h:.0f}h ago — no update since yesterday"
    return "ok", f"Last updated {age_h:.1f}h ago"


def check_session(portfolio: dict) -> tuple:
    s = portfolio.get("session", {})
    if not s.get("active"):
        return "error", "Session is INACTIVE — reset needed or session complete"
    day = s.get("current_day", 0)
    total = s.get("total_days", SESSION_DAYS)
    if day > total:
        return "error", f"Day {day} exceeds total {total} — session overrun"
    days_left = total - day
    return "ok", f"Day {day}/{total} ({days_left} days remaining)"


def check_circuit_breaker(portfolio: dict) -> tuple:
    cb = portfolio.get("circuit_breaker", {})
    if cb.get("triggered"):
        return "error", f"CIRCUIT BREAKER: {cb.get('reason', 'triggered')}"
    return "ok", "Inactive"


def check_deployment(portfolio: dict) -> tuple:
    equity = portfolio.get("equity", INITIAL_CAPITAL)
    cash   = portfolio.get("cash", equity)
    n_pos  = len(portfolio.get("positions", {}))
    if equity <= 0:
        return "warn", "Equity is zero"
    deployed_pct = (equity - cash) / equity * 100
    day = portfolio.get("session", {}).get("current_day", 0)

    # Grace period — first 3 days of session, low deployment is expected
    if day <= 3:
        return "ok", f"{deployed_pct:.0f}% deployed ({n_pos} positions) — early session"
    if deployed_pct < 20:
        return "error", f"Only {deployed_pct:.0f}% deployed ({n_pos} positions) — critical idle cash"
    if deployed_pct < 40:
        return "warn", f"{deployed_pct:.0f}% deployed ({n_pos} positions) — under-deployment risk"
    return "ok", f"{deployed_pct:.0f}% deployed ({n_pos} positions)"


def check_drawdown(portfolio: dict) -> tuple:
    dd = portfolio.get("stats", {}).get("max_drawdown_pct")
    if dd is None:
        peak   = portfolio.get("peak_equity", INITIAL_CAPITAL)
        equity = portfolio.get("equity", INITIAL_CAPITAL)
        dd = (peak - equity) / peak * 100 if peak > 0 else 0
    if dd >= 15:
        return "error", f"{dd:.1f}% — AT/ABOVE circuit breaker threshold (15%)"
    if dd >= 10:
        return "warn", f"{dd:.1f}% — approaching circuit breaker (15%)"
    if dd >= 5:
        return "warn", f"{dd:.1f}% — elevated, monitor closely"
    return "ok", f"{dd:.1f}%"


_DAILY_PIPELINES = [
    "premarket_check", "morning_session", "penny_scan", "midcap_scan", "eod_session",
]


def check_pipeline_freshness(portfolio: dict, date_str: str) -> tuple:
    """
    Catches the failure mode that let morning_session.py silently skip for 19 days
    (2026-08-07 to 2026-08-25): each workflow's dst-gate job set skip=true every run,
    but the workflow still reported "success" in the Actions UI (a skipped job doesn't
    count as a failure). qa-analyst.yml only reacts to workflow_run conclusion=='failure',
    so it structurally cannot see a "succeeded but skipped everything" run — this check
    has to live here instead, on its own unconditional schedule.

    Uses last_run_dates (stamped by mark_ran_today() in each script) rather than
    journal/signal entries, since a quiet-but-healthy no-trade day looks identical to a
    silently-skipped day if you only look at whether anything was written.
    """
    if not portfolio.get("session", {}).get("active"):
        return "ok", "Session inactive — freshness check skipped"
    last_run = portfolio.get("last_run_dates", {})
    stale = [name for name in _DAILY_PIPELINES if last_run.get(name) != date_str]
    if not stale:
        return "ok", "All scheduled pipelines ran today"
    status = "error" if len(stale) >= 2 else "warn"
    return status, f"Did not run today: {', '.join(stale)} — check dst-gate windows"


def check_alpha(portfolio: dict) -> tuple:
    equity  = portfolio.get("equity", INITIAL_CAPITAL)
    initial = portfolio.get("initial_capital", INITIAL_CAPITAL)
    our_ret = (equity - initial) / initial * 100 if initial > 0 else 0
    spy_ret = portfolio.get("stats", {}).get("benchmark_return_pct") or 0
    alpha   = our_ret - spy_ret
    label   = f"us {our_ret:+.1f}% vs SPY {spy_ret:+.1f}%"
    if alpha < -10:
        return "error", f"Alpha {alpha:+.1f}% ({label}) — severe underperformance"
    if alpha < -5:
        return "warn",  f"Alpha {alpha:+.1f}% ({label}) — significant gap"
    if alpha < 0:
        return "warn",  f"Alpha {alpha:+.1f}% ({label}) — trailing benchmark"
    return "ok", f"Alpha {alpha:+.1f}% ({label})"


# ──────────────────────────────────────────────────────────────────────────────
# Report builders
# ──────────────────────────────────────────────────────────────────────────────

_ICON    = {"ok": "✅", "warn": "⚠️", "error": "🔴"}
_TRAFFIC = {"ok": "🟢", "warn": "🟡", "error": "🔴"}


def _overall(checks: list) -> str:
    statuses = [c[0] for c in checks]
    if "error" in statuses:
        return "error"
    if "warn" in statuses:
        return "warn"
    return "ok"


def _check_lines(labels: list, checks: list) -> str:
    return "\n".join(
        f"  {_ICON[st]}  <b>{lbl}:</b> {msg}"
        for lbl, (st, msg) in zip(labels, checks)
    )


def _positions_block(portfolio: dict) -> str:
    positions = portfolio.get("positions", {})
    if not positions:
        return "  None"
    lines = []
    for t, p in positions.items():
        direction = "SHORT" if p.get("direction") == "short" else "LONG"
        entry  = p.get("entry_price", 0)
        last   = p.get("last_price", entry)
        pnl_pct = (last - entry) / entry * 100 if direction == "LONG" and entry else 0
        if direction == "SHORT" and entry:
            pnl_pct = (entry - last) / entry * 100
        lines.append(
            f"  • <b>{t}</b> {direction} {p.get('qty')} @ ${entry:.2f}"
            f" → ${last:.2f} ({pnl_pct:+.1f}%)"
        )
    return "\n".join(lines)


def build_preflight(portfolio: dict, date_str: str) -> str:
    s     = portfolio.get("session", {})
    day   = s.get("current_day", 0)
    total = s.get("total_days", SESSION_DAYS)

    labels = ["Workflow freshness", "Session state", "Circuit breaker", "Deployment", "Max drawdown", "Alpha vs SPY"]
    checks = [
        check_staleness(portfolio),
        check_session(portfolio),
        check_circuit_breaker(portfolio),
        check_deployment(portfolio),
        check_drawdown(portfolio),
        check_alpha(portfolio),
    ]
    traffic = _TRAFFIC[_overall(checks)]

    return (
        f"{traffic} <b>PRE-FLIGHT — {date_str} | Day {day}/{total}</b>\n\n"
        f"{_check_lines(labels, checks)}\n\n"
        f"<b>Open Positions:</b>\n{_positions_block(portfolio)}"
    )


def build_recap(portfolio: dict, date_str: str) -> str:
    s       = portfolio.get("session", {})
    day     = s.get("current_day", 0)
    total   = s.get("total_days", SESSION_DAYS)
    equity  = portfolio.get("equity", INITIAL_CAPITAL)
    initial = portfolio.get("initial_capital", INITIAL_CAPITAL)
    our_ret = (equity - initial) / initial * 100 if initial > 0 else 0
    spy_ret = portfolio.get("stats", {}).get("benchmark_return_pct") or 0
    alpha   = our_ret - spy_ret

    labels = ["Workflow freshness", "Pipeline freshness", "Circuit breaker", "Deployment", "Max drawdown", "Alpha vs SPY"]
    checks = [
        check_staleness(portfolio),
        check_pipeline_freshness(portfolio, date_str),
        check_circuit_breaker(portfolio),
        check_deployment(portfolio),
        check_drawdown(portfolio),
        check_alpha(portfolio),
    ]
    traffic = _TRAFFIC[_overall(checks)]

    # Pipeline stats from audit log
    audit = _load_audit_log(date_str)
    if audit:
        analyzed = sum(1 for v in audit.values() if v.get("llm_started"))
        executed = sum(1 for v in audit.values() if v.get("executed"))
        skipped  = len(audit) - analyzed
        audit_line = f"  Pipeline: {len(audit)} tickers → {analyzed} analyzed, {executed} executed, {skipped} skipped"
    else:
        audit_line = "  Pipeline: audit log not yet available for today"

    # Today's closed trades
    today_trades = [t for t in portfolio.get("trade_history", []) if t.get("closed_date") == date_str]
    if today_trades:
        pnl_total  = sum(t.get("pnl", 0) for t in today_trades)
        trade_rows = "\n".join(
            f"  • {t['ticker']}: {t.get('pnl_pct', 0):+.1f}% (${t.get('pnl', 0):+.2f}) — {t.get('reason', '?')}"
            for t in today_trades
        )
        trades_block = f"<b>Today's Closed Trades:</b>\n{trade_rows}\n  Net P&L: ${pnl_total:+.2f}"
    else:
        trades_block = "<b>Today's Closed Trades:</b> None"

    return (
        f"{traffic} <b>EOD RECAP — {date_str} | Day {day}/{total}</b>\n\n"
        f"Equity: <b>${equity:,.2f}</b> ({our_ret:+.1f}%) | SPY: {spy_ret:+.1f}% | "
        f"Alpha: <b>{alpha:+.1f}%</b>\n\n"
        f"{_check_lines(labels, checks)}\n"
        f"{audit_line}\n\n"
        f"{trades_block}\n\n"
        f"<b>Open Positions:</b>\n{_positions_block(portfolio)}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["preflight", "recap"], default=None,
                        help="Force a specific mode (default: infer from ET time of day)")
    args = parser.parse_args()

    now_et   = _et_now()
    date_str = now_et.strftime("%Y-%m-%d")

    if args.mode == "preflight":
        is_recap = False
    elif args.mode == "recap":
        is_recap = True
    else:
        is_recap = now_et.hour >= 12

    mode = "EOD RECAP" if is_recap else "PRE-FLIGHT"
    print(f"[health] Mode: {mode} | ET: {now_et.strftime('%H:%M')} | Date: {date_str}")

    portfolio = _load_portfolio()
    if not portfolio:
        send_private_only(
            "🔴 <b>HEALTH CHECK FAILED</b>\n\n"
            "Could not load portfolio.json — file missing or corrupt.\n"
            "Check GitHub Actions logs immediately."
        )
        print("[health] ERROR: portfolio.json not found or empty.")
        sys.exit(1)

    report = build_recap(portfolio, date_str) if is_recap else build_preflight(portfolio, date_str)
    send_private_only(report)
    print(f"[health] {mode} report sent to private Telegram.")


if __name__ == "__main__":
    main()
