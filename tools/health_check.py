"""
LLM provider health check — called at the start of morning_session.py.
Tests each provider with a 1-token call before any trading begins.
Sends a Telegram alert immediately if any provider is unreachable.

Returns a dict of {provider: True/False} and the overall health status.
"""
import os
import sys
import time

import litellm
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MODELS

_TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")
_DASHBOARD      = "https://outtcom.github.io/ai-paper-trading/"

# Map provider → (model_key, display_name, agents_affected)
_PROVIDERS = [
    ("fast",     "Groq llama-3.3-70b",  "Fundamental / Sentiment / Technical / Risk Manager"),
    ("debate",   "OpenAI gpt-4o-mini",  "Bull & Bear Researchers"),
    ("analyst",  "Anthropic Sonnet",     "Trader"),
    ("decision", "Anthropic Opus",       "Fund Manager"),
]


def _test_provider(model_key: str) -> tuple[bool, str]:
    """Call the model with max_tokens=1. Returns (ok, error_message)."""
    try:
        litellm.completion(
            model=MODELS[model_key],
            max_tokens=1,
            messages=[{"role": "user", "content": "1"}],
            timeout=20,
        )
        return True, ""
    except Exception as e:
        return False, str(e)[:300]


def _send_alert(failed: list[tuple[str, str, str, str]]) -> None:
    """Send a Telegram alert listing each failed provider and affected agents."""
    if not _TELEGRAM_TOKEN or not _TELEGRAM_CHAT:
        return

    lines = ["🚨 <b>LLM Health Check — Provider(s) DOWN</b>\n"]
    for model_key, display, agents, error in failed:
        lines.append(f"❌ <b>{display}</b>")
        lines.append(f"   Agents: {agents}")
        lines.append(f"   Error:  <code>{error[:200]}</code>\n")

    lines.append("⚠️ Trading pipeline will run with degraded analysis. Check API keys and credits.")
    lines.append(f'\n📊 <a href="{_DASHBOARD}">Live Dashboard</a>')

    requests.post(
        f"https://api.telegram.org/bot{_TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": _TELEGRAM_CHAT,
            "text":   "\n".join(lines),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )


def run() -> dict:
    """
    Test all LLM providers. Sends a Telegram alert for any failures.
    Returns {"groq": True, "openai": True, "anthropic_sonnet": True, "anthropic_opus": True,
             "all_healthy": bool, "failed_count": int}
    """
    print("[health_check] Testing LLM providers...")
    results = {}
    failed  = []

    for model_key, display, agents in _PROVIDERS:
        ok, err = _test_provider(model_key)
        label   = display.split()[0].lower()  # groq / openai / anthropic
        results[f"{label}_{model_key}"] = ok
        status  = "✅ OK" if ok else f"❌ FAIL — {err[:80]}"
        print(f"[health_check]   {display}: {status}")
        if not ok:
            failed.append((model_key, display, agents, err))
        time.sleep(0.5)   # avoid rate-limit burst

    results["all_healthy"]  = len(failed) == 0
    results["failed_count"] = len(failed)

    if failed:
        _send_alert(failed)
        print(f"[health_check] ⚠️  {len(failed)} provider(s) down — alert sent to Telegram.")
    else:
        print("[health_check] All providers healthy.")

    return results


def check_pipeline_errors(all_results: dict, date: str) -> None:
    """
    After _analyze_all() runs, scan for agent errors across all tickers.
    Sends a Telegram alert if any agent type is failing consistently.
    Called from morning_session.py after the pipeline finishes.
    """
    agent_fail_counts = {
        "fundamental_analyst": 0,
        "sentiment_analyst": 0,
        "technical_analyst": 0,
        "trader": 0,
        "risk_manager": 0,
        "fund_manager": 0,
    }
    total_tickers = 0

    for ticker, state in all_results.items():
        if not isinstance(state, dict):
            continue
        errors = state.get("errors", [])
        if not errors:
            continue
        total_tickers += 1
        for err_entry in errors:
            agent = err_entry.get("agent", "")
            if agent in agent_fail_counts:
                agent_fail_counts[agent] += 1

    failing_agents = {a: n for a, n in agent_fail_counts.items() if n > 0}
    if not failing_agents or not total_tickers:
        return

    # Only alert if failures are widespread (>1 ticker affected per agent)
    widespread = {a: n for a, n in failing_agents.items() if n > 1}
    if not widespread:
        return

    if not _TELEGRAM_TOKEN or not _TELEGRAM_CHAT:
        return

    lines = [f"⚠️ <b>Pipeline Health Warning — {date}</b>\n",
             "Agent errors detected across multiple tickers:\n"]
    for agent, count in widespread.items():
        lines.append(f"  • <b>{agent}</b>: failed on {count} ticker(s)")

    lines.append(
        "\n<i>Analysis may be incomplete. Check Actions logs for error details.</i>"
        f'\n\n📊 <a href="{_DASHBOARD}">Live Dashboard</a>'
    )

    requests.post(
        f"https://api.telegram.org/bot{_TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": _TELEGRAM_CHAT,
            "text":   "\n".join(lines),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    print(f"[health_check] Pipeline error alert sent — {widespread}")
