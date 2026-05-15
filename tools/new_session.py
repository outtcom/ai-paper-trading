"""
New Session Reset Script
========================
Run this ONCE after a session ends to:
  1. Load the current (completed) portfolio.json
  2. Run the performance analyst LLM to generate a post-mortem review
  3. Send the review + recommendations to Telegram (private chat)
  4. Archive the completed portfolio to docs/portfolio_session<N>_<start_date>.json
  5. Reset portfolio.json to a fresh 30-day session
  6. Send a "Session N+1 started" confirmation to Telegram

SAFE TRIGGER WINDOW:
  After 22:00 UTC on a weekday, or any time on a weekend.
  Triggering during 05:00–22:00 UTC risks colliding with scheduled GHA workflows.
  The GitHub Actions workflow wrapping this script enforces the UTC time guard.
"""
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Resolve paths relative to this file
_TOOLS_DIR   = Path(__file__).parent
_SYSTEM_DIR  = _TOOLS_DIR.parent
PORTFOLIO_FILE = _SYSTEM_DIR / "docs" / "portfolio.json"
DOCS_DIR       = _SYSTEM_DIR / "docs"
STATE_DIR      = _SYSTEM_DIR / ".tmp" / "state"

sys.path.insert(0, str(_SYSTEM_DIR))

from agents.performance_analyst import run as analyse_session, format_telegram_message
from tools.session_manager import start_session, TOTAL_DAYS, INITIAL_CAPITAL
from tools.telegram_bot import send_message, broadcast_message


def _load_portfolio() -> dict:
    with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _archive_portfolio(portfolio: dict, session_num: int, start_date: str) -> Path:
    """Copy portfolio.json to docs/portfolio_session<N>_<date>.json."""
    archive_name = f"portfolio_session{session_num}_{start_date}.json"
    archive_path = DOCS_DIR / archive_name
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(portfolio, f, indent=2)
    print(f"[new_session] Archived to {archive_path}")
    return archive_path


def _save_review(review: dict, session_num: int) -> None:
    """Save the performance review JSON to .tmp/state/."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = STATE_DIR / today
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"performance_review_session{session_num}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(review, f, indent=2)
    print(f"[new_session] Review saved to {path}")


def main() -> None:
    portfolio = _load_portfolio()
    session   = portfolio.get("session", {})

    # Determine session metadata
    session_num  = session.get("session_number", 1)
    start_date   = session.get("start_date", "unknown")
    current_day  = session.get("current_day", 0)
    total_days   = session.get("total_days", TOTAL_DAYS)
    equity       = portfolio.get("equity", INITIAL_CAPITAL)
    initial      = portfolio.get("initial_capital", INITIAL_CAPITAL)
    our_return   = round((equity - initial) / initial * 100, 2)
    spy_return   = round(portfolio.get("stats", {}).get("benchmark_return_pct", 0) or 0, 2)

    print(f"[new_session] Reviewing Session {session_num} (Day {current_day}/{total_days})")
    print(f"[new_session] Return: {our_return:+.2f}%  |  SPY: {spy_return:+.2f}%  |  Alpha: {our_return - spy_return:+.2f}%")

    # 1. Run performance analyst
    print("[new_session] Running performance analyst...")
    try:
        review = analyse_session(portfolio)
    except Exception as e:
        print(f"[new_session] Performance analyst failed: {e}")
        review = {
            "session_alpha": round(our_return - spy_return, 2),
            "primary_failures": [f"Analyst error: {e}"],
            "primary_successes": [],
            "parameter_recommendations": [],
            "strategy_recommendations": [],
            "session2_targets": {"min_alpha_vs_spy": 2.0, "target_deployment_pct": 70.0, "target_win_rate": 0.55},
        }

    # 2. Save review locally
    _save_review(review, session_num)

    # 3. Send review to Telegram (private only — detailed)
    print("[new_session] Sending performance review to Telegram...")
    tg_msg = format_telegram_message(review, session_num)
    send_message(tg_msg)

    # 4. Archive the completed portfolio
    archive_path = _archive_portfolio(portfolio, session_num, start_date)

    # 5. Reset portfolio to fresh session
    print("[new_session] Resetting portfolio for new session...")
    new_portfolio = start_session()
    new_session_num = session_num + 1
    # Record session number in portfolio
    new_portfolio["session"]["session_number"] = new_session_num
    from tools.session_manager import _save as _save_portfolio
    _save_portfolio(new_portfolio)

    # 6. Send "Session N+1 started" confirmation
    next_day = new_portfolio["session"].get("current_day", 1)
    started_msg = (
        f"🚀 <b>Session {new_session_num} Started</b>\n\n"
        f"Day {next_day} of {TOTAL_DAYS} — 30-day forward test\n"
        f"Capital: ${INITIAL_CAPITAL:,.2f}\n\n"
        f"🎯 Target: Beat SPY\n"
        f"📊 Watch: outtcom.github.io/ai-paper-trading"
    )
    broadcast_message(started_msg)
    print(f"[new_session] Session {new_session_num} started. All done.")


if __name__ == "__main__":
    main()
