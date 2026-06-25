"""
Pause Session
=============
Disables all trading workflows (cron triggers) via GitHub API and sets a
paused flag in portfolio.json. Monitoring workflows (health-report,
weekly-briefing, qa-analyst) remain active.

Existing positions are HELD — no forced liquidation. No new trades execute.

Usage:
  python pause_session.py --reason "travelling until 2026-07-05"
  python pause_session.py  # prompts for reason
"""
import argparse
import json
import subprocess
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO           = "outtcom/ai-paper-trading"
PORTFOLIO_FILE = Path(__file__).parent / "docs" / "portfolio.json"

# Workflows that stop trading. Monitoring workflows intentionally excluded.
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
    parser = argparse.ArgumentParser(description="Pause all trading workflows")
    parser.add_argument("--reason", default=None, help="Why you are pausing")
    args = parser.parse_args()

    reason = args.reason
    if not reason:
        reason = input("Pause reason (required): ").strip()
    if not reason:
        print("ERROR: A reason is required to pause the session.")
        sys.exit(1)

    # ── Sanity check: portfolio must be active ───────────────────────────────
    with open(PORTFOLIO_FILE) as f:
        portfolio = json.load(f)

    if not portfolio.get("session", {}).get("active"):
        print("ERROR: No active session to pause.")
        sys.exit(1)

    if portfolio["session"].get("paused"):
        print(f"Session is already paused. Reason: {portfolio['session'].get('pause_reason')}")
        print("Run resume_session.py to resume first.")
        sys.exit(1)

    # ── Disable trading workflows ────────────────────────────────────────────
    print(f"\nPausing session — {reason}\n")
    disabled = []
    failed   = []

    for wf in TRADING_WORKFLOWS:
        code, out = _gh("workflow", "disable", wf)
        if code == 0:
            print(f"  ✅ Disabled: {wf}")
            disabled.append(wf)
        else:
            print(f"  ❌ Failed:   {wf} — {out}")
            failed.append(wf)

    if failed:
        print(f"\nWARNING: {len(failed)} workflow(s) could not be disabled: {failed}")
        print("Check GitHub → Actions → Manage workflows manually if needed.")

    # ── Record pause in portfolio.json ───────────────────────────────────────
    portfolio["session"]["paused"]       = True
    portfolio["session"]["pause_reason"] = reason
    portfolio["session"]["paused_at"]    = datetime.now(timezone.utc).isoformat()
    portfolio["session"]["paused_workflows"] = disabled

    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)

    # ── Commit + push ────────────────────────────────────────────────────────
    day     = portfolio["session"]["current_day"]
    total   = portfolio["session"]["total_days"]
    equity  = portfolio.get("equity", 0)
    n_pos   = len(portfolio.get("positions", {}))

    subprocess.run(["git", "add", "docs/portfolio.json"], check=True)
    subprocess.run(
        ["git", "commit", "-m", f"chore: pause session — Day {day}/{total} — {reason}"],
        check=True,
    )
    subprocess.run(["git", "pull", "--rebase", "-X", "ours"], check=True)
    subprocess.run(["git", "push"], check=True)

    print(f"""
Session PAUSED
==============
Reason:   {reason}
Day:      {day}/{total}
Equity:   ${equity:,.2f}
Positions: {n_pos} open (held, not liquidated)
Disabled: {len(disabled)}/{len(TRADING_WORKFLOWS)} trading workflows

Still active: health-report, weekly-briefing, qa-analyst

To resume:  python resume_session.py
""")


if __name__ == "__main__":
    main()
