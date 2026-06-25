"""
Resume Session
==============
Re-enables all trading workflows disabled by pause_session.py and clears
the paused flag in portfolio.json.

Usage:
  python resume_session.py
"""
import json
import subprocess
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO           = "outtcom/ai-paper-trading"
PORTFOLIO_FILE = Path(__file__).parent / "docs" / "portfolio.json"

TRADING_WORKFLOWS = [
    "morning-session.yml",
    "eod-session.yml",
    "midday-check.yml",
    "preclose-alert.yml",
    "premarket-check.yml",
    "midcap-scan.yml",
    "penny-scan.yml",
    "insider-scan.yml",
]


def _gh(*args: str) -> tuple[int, str]:
    result = subprocess.run(
        ["gh", *args, "--repo", REPO],
        capture_output=True, text=True,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def main() -> None:
    # ── Sanity checks ────────────────────────────────────────────────────────
    with open(PORTFOLIO_FILE) as f:
        portfolio = json.load(f)

    if not portfolio.get("session", {}).get("active"):
        print("ERROR: No active session.")
        sys.exit(1)

    if not portfolio["session"].get("paused"):
        print("Session is not currently paused — nothing to resume.")
        sys.exit(0)

    pause_reason = portfolio["session"].get("pause_reason", "unknown")
    paused_at    = portfolio["session"].get("paused_at", "unknown")
    print(f"\nResuming session (paused: {pause_reason} @ {paused_at[:10]})\n")

    # ── Re-enable trading workflows ──────────────────────────────────────────
    # Enable everything in TRADING_WORKFLOWS regardless of what was recorded
    # in paused_workflows — safer in case the list drifts.
    enabled = []
    failed  = []

    for wf in TRADING_WORKFLOWS:
        code, out = _gh("workflow", "enable", wf)
        if code == 0:
            print(f"  ✅ Enabled: {wf}")
            enabled.append(wf)
        else:
            print(f"  ❌ Failed:  {wf} — {out}")
            failed.append(wf)

    if failed:
        print(f"\nWARNING: {len(failed)} workflow(s) could not be re-enabled: {failed}")
        print("Enable them manually at: https://github.com/outtcom/ai-paper-trading/actions")

    # ── Clear pause flag in portfolio.json ───────────────────────────────────
    session = portfolio["session"]
    session["paused"]            = False
    session["pause_reason"]      = None
    session["paused_at"]         = None
    session["resumed_at"]        = datetime.now(timezone.utc).isoformat()
    session.pop("paused_workflows", None)

    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)

    # ── Commit + push ────────────────────────────────────────────────────────
    day    = session["current_day"]
    total  = session["total_days"]
    equity = portfolio.get("equity", 0)
    n_pos  = len(portfolio.get("positions", {}))

    subprocess.run(["git", "add", "docs/portfolio.json"], check=True)
    subprocess.run(
        ["git", "commit", "-m", f"chore: resume session — Day {day}/{total}"],
        check=True,
    )
    subprocess.run(["git", "pull", "--rebase", "-X", "ours"], check=True)
    subprocess.run(["git", "push"], check=True)

    print(f"""
Session RESUMED
===============
Day:      {day}/{total}
Equity:   ${equity:,.2f}
Positions: {n_pos} open
Enabled:  {len(enabled)}/{len(TRADING_WORKFLOWS)} trading workflows

Next scheduled run: morning session tomorrow at ~9:45 AM ET.
""")


if __name__ == "__main__":
    main()
