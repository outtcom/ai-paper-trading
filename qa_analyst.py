"""
QA Analyst — triggered automatically after any workflow failure.

Downloads the failed run's logs from GitHub, sends them to an LLM for
diagnosis, applies a code fix when confidence is high, commits + pushes,
retriggests the failed workflow, and alerts via Telegram in all cases.

Environment vars (set by the GitHub Actions workflow):
  GITHUB_TOKEN         — auto-injected by Actions (read logs + push)
  FAILED_RUN_ID        — run ID of the workflow that failed
  FAILED_WORKFLOW_NAME — display name of the failed workflow
"""
import io
import json
import os
import subprocess
import sys
import zipfile

import requests
from dotenv import load_dotenv
from litellm import completion

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
REPO            = os.environ.get("GITHUB_REPOSITORY", "outtcom/ai-paper-trading")
FAILED_RUN_ID   = os.environ.get("FAILED_RUN_ID", "")
FAILED_WORKFLOW = os.environ.get("FAILED_WORKFLOW_NAME", "unknown")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")

# Only edit Python scripts — never touch YAML, JSON data files, or .env
EDITABLE_EXT       = {".py"}
NON_EDITABLE_PATHS = {"docs/portfolio.json", ".env", "requirements.txt", "config.py"}

# Auto-fix only when model is this confident (0–1)
MIN_CONFIDENCE = 0.75

GH_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Workflow name → yml file (for retrigger via dispatch API)
WORKFLOW_FILES = {
    "Pre-Market Gap Scanner":       "premarket-check.yml",
    "Morning Session":              "morning-session.yml",
    "ORB Scalping Scan":            "scalping-scan.yml",
    "Midday Position Monitor":      "midday-check.yml",
    "Pre-Close Alert":              "preclose-alert.yml",
    "End-of-Day Session":           "eod-session.yml",
    "Weekly Intelligence Briefing":              "weekly-briefing.yml",
    "Insider Activity + Political Signal Scan":  "insider-scan.yml",
}


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def get_run_logs(run_id: str) -> str:
    url  = f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}/logs"
    resp = requests.get(url, headers=GH_HEADERS, allow_redirects=True, timeout=30)
    if resp.status_code != 200:
        print(f"[qa] Log download failed: HTTP {resp.status_code}")
        return ""
    try:
        z     = zipfile.ZipFile(io.BytesIO(resp.content))
        parts = []
        for name in sorted(z.namelist()):
            parts.append(f"=== {name} ===\n{z.read(name).decode('utf-8', errors='replace')}")
        full = "\n\n".join(parts)
        return full[-18000:]  # last 18 K chars — enough to capture the full traceback
    except Exception as e:
        print(f"[qa] Log parse error: {e}")
        return ""


def retrigger_workflow(workflow_name: str) -> bool:
    yml = WORKFLOW_FILES.get(workflow_name)
    if not yml:
        return False
    url  = f"https://api.github.com/repos/{REPO}/actions/workflows/{yml}/dispatches"
    resp = requests.post(url, headers=GH_HEADERS, json={"ref": "main"}, timeout=15)
    return resp.status_code == 204


# ---------------------------------------------------------------------------
# LLM diagnosis
# ---------------------------------------------------------------------------

DIAGNOSIS_PROMPT = """\
You are a QA engineer for a Python stock-trading automation system running on GitHub Actions.
The workflow "{workflow}" just failed. Here are the full logs:

{logs}

Task:
1. Find the Python traceback or error message.
2. Identify the root cause — be specific (file, function, error type, line if visible).
3. Decide if it is fixable by editing a Python source file.
   - Code bug (NameError, AttributeError, wrong logic, etc.) → fixable.
   - API / network outage, rate-limit, missing GitHub secret → NOT fixable via code.
4. If fixable, provide the *minimal* change: exact old text and replacement.
   The old_code must exist verbatim in the file — do not paraphrase.

Respond with ONLY valid JSON (no markdown fences):
{{
  "root_cause":  "one sentence describing what broke",
  "fix_type":    "code_bug | api_outage | rate_limit | missing_secret | data_issue | unknown",
  "fixable":     true | false,
  "confidence":  0.0-1.0,
  "file_path":   "relative/path/to/file.py  (null if not fixable)",
  "old_code":    "exact verbatim string to replace  (null if not fixable)",
  "new_code":    "replacement string  (null if not fixable)",
  "explanation": "one sentence: what the fix does"
}}"""


def diagnose(logs: str) -> dict:
    try:
        resp = completion(
            model="openai/gpt-4o-mini",
            messages=[{"role": "user", "content": DIAGNOSIS_PROMPT.format(
                workflow=FAILED_WORKFLOW, logs=logs
            )}],
            response_format={"type": "json_object"},
            timeout=90,
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        print(f"[qa] LLM call failed: {e}")
        return {
            "root_cause": f"LLM unavailable: {e}",
            "fix_type": "unknown",
            "fixable": False,
            "confidence": 0.0,
        }


# ---------------------------------------------------------------------------
# Fix application
# ---------------------------------------------------------------------------

def apply_fix(d: dict) -> bool:
    if not d.get("fixable"):
        return False
    if d.get("confidence", 0) < MIN_CONFIDENCE:
        print(f"[qa] Confidence {d.get('confidence'):.0%} < {MIN_CONFIDENCE:.0%} — skipping auto-fix")
        return False

    fp       = d.get("file_path")
    old_code = d.get("old_code")
    new_code = d.get("new_code")

    if not fp or not old_code or new_code is None:
        print("[qa] Incomplete fix fields — skipping")
        return False
    if os.path.splitext(fp)[1] not in EDITABLE_EXT:
        print(f"[qa] {fp} is not a .py file — skipping")
        return False
    if fp in NON_EDITABLE_PATHS:
        print(f"[qa] {fp} is protected — skipping")
        return False
    if not os.path.exists(fp):
        print(f"[qa] {fp} not found — skipping")
        return False

    with open(fp, "r", encoding="utf-8") as f:
        content = f.read()

    if old_code not in content:
        print(f"[qa] old_code not found verbatim in {fp} — skipping")
        return False

    with open(fp, "w", encoding="utf-8") as f:
        f.write(content.replace(old_code, new_code, 1))

    print(f"[qa] Fix applied to {fp}")
    return True


def commit_and_push(file_path: str) -> bool:
    try:
        subprocess.run(["git", "config", "user.email", "qa-analyst[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "config", "user.name",  "qa-analyst[bot]"], check=True)
        subprocess.run(["git", "add", file_path], check=True)
        result = subprocess.run(
            ["git", "commit", "-m", f"fix(qa): auto-fix {FAILED_WORKFLOW} — {file_path}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0 and "nothing to commit" in result.stdout + result.stderr:
            print("[qa] Nothing new to commit")
            return False
        subprocess.run(["git", "pull", "--rebase", "-X", "ours"], check=True)
        subprocess.run(["git", "push"], check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[qa] Git error: {e}")
        return False


# ---------------------------------------------------------------------------
# Telegram alert
# ---------------------------------------------------------------------------

def send_alert(d: dict, fix_applied: bool, retriggered: bool, logs: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return

    icon = "✅" if fix_applied else ("🟡" if d.get("fixable") else "🔴")
    if fix_applied and retriggered:
        status = "Auto-fixed and retriggered"
    elif fix_applied:
        status = "Auto-fixed — check Actions tab to verify, then retrigger manually"
    elif d.get("fixable") and d.get("confidence", 0) < MIN_CONFIDENCE:
        status = f"Low confidence ({d.get('confidence', 0):.0%}) — manual review needed"
    else:
        status = "Needs manual review"

    text = (
        f"{icon} <b>QA Analyst — {FAILED_WORKFLOW}</b>\n\n"
        f"<b>Root cause:</b> {d.get('root_cause', 'Unknown')}\n"
        f"<b>Type:</b> {d.get('fix_type', 'unknown')}\n"
        f"<b>Status:</b> {status}"
    )

    if fix_applied:
        text += (
            f"\n\n<b>File fixed:</b> <code>{d.get('file_path')}</code>\n"
            f"<i>{d.get('explanation', '')}</i>"
        )
    elif not d.get("fixable"):
        # Show last 15 log lines for manual debugging
        snippet = "\n".join(logs.strip().splitlines()[-15:])
        text += f"\n\n<pre>{snippet[:1200]}</pre>"

    footer = '\n\n📊 <a href="https://outtcom.github.io/ai-paper-trading/">Live Dashboard</a>'
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT, "text": text + footer,
              "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=15,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if not FAILED_RUN_ID:
        print("[qa] FAILED_RUN_ID not set — nothing to analyse")
        sys.exit(0)

    print(f"[qa] ===== QA Analyst — run {FAILED_RUN_ID} ({FAILED_WORKFLOW}) =====")

    logs = get_run_logs(FAILED_RUN_ID)
    if not logs:
        print("[qa] Could not retrieve logs — exiting")
        sys.exit(0)
    print(f"[qa] Retrieved {len(logs):,} chars of logs")

    d = diagnose(logs)
    print(f"[qa] Diagnosis:\n{json.dumps(d, indent=2)}")

    fix_applied = apply_fix(d)
    pushed      = commit_and_push(d.get("file_path", "")) if fix_applied else False
    retriggered = retrigger_workflow(FAILED_WORKFLOW)     if pushed      else False

    send_alert(d, fix_applied, retriggered, logs)
    print(f"[qa] Done — fix_applied={fix_applied} pushed={pushed} retriggered={retriggered}")


if __name__ == "__main__":
    main()
