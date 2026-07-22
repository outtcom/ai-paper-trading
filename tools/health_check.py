"""
LLM provider health check — called at the start of morning_session.py.
Tests each provider with a 1-token call before any trading begins.
Sends a Telegram alert immediately if any provider is unreachable.

Also provides post-run diagnostics via check_pipeline_errors() and check_groq_quota().

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


def _send_telegram(text: str) -> None:
    if not _TELEGRAM_TOKEN or not _TELEGRAM_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{_TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id":                  _TELEGRAM_CHAT,
                "text":                     text,
                "parse_mode":               "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
    except Exception as e:
        print(f"[health_check] Telegram send failed: {e}")


def _send_alert(failed: list, anthropic_down: bool) -> None:
    """Send a Telegram alert to PRIVATE chat only. Severity depends on which providers are down."""
    if anthropic_down:
        header = "🔴 <b>CRITICAL — Anthropic API Down</b>"
        impact = "Trading <b>HALTED today</b> — Trader and Fund Manager cannot run. Session equity recorded; no new positions."
    else:
        header = "⚠️ <b>WARNING — Analysis Provider Down</b>"
        impact = "Pipeline will run with stub outputs. Trades may still execute with incomplete analysis."

    lines = [header, "", impact, ""]
    for model_key, display, agents, error in failed:
        lines.append(f"❌ <b>{display}</b>")
        lines.append(f"   Agents: {agents}")
        lines.append(f"   Error: <code>{error[:200]}</code>")
        lines.append("")

    lines.append("Check API keys and credits.")
    lines.append(f'\n📊 <a href="{_DASHBOARD}">Live Dashboard</a>')
    _send_telegram("\n".join(lines))


def run() -> dict:
    """
    Test all LLM providers. Sends a private Telegram alert for any failures.

    Returns:
      {
        "groq_fast": bool, "openai_debate": bool,
        "anthropic_analyst": bool, "anthropic_decision": bool,
        "groq_ok":      bool,   # Fundamental / Sentiment / Technical / Risk Manager
        "openai_ok":    bool,   # Bull & Bear Researchers
        "anthropic_ok": bool,   # Trader + Fund Manager (CRITICAL — halt if False)
        "all_healthy":  bool,
        "failed_count": int,
      }
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

    # Structured provider flags for routing decisions in morning_session.py
    results["groq_ok"]      = bool(results.get("groq_fast"))
    results["openai_ok"]    = bool(results.get("openai_debate"))
    results["anthropic_ok"] = bool(results.get("anthropic_analyst")) and bool(results.get("anthropic_decision"))
    results["all_healthy"]  = len(failed) == 0
    results["failed_count"] = len(failed)

    if failed:
        anthropic_down = not results["anthropic_ok"]
        _send_alert(failed, anthropic_down)
        print(f"[health_check] ⚠️  {len(failed)} provider(s) down — alert sent to private Telegram.")
    else:
        print("[health_check] All providers healthy.")

    return results


def check_groq_quota(date: str) -> None:
    """
    Check today's Groq token usage and send a proactive Telegram warning if
    failover was activated during this session's pipeline run.
    Called after _analyze_all() so the counter reflects real usage.
    """
    try:
        from tools.groq_quota import get_today_usage, is_failover_active, _DAILY_LIMIT, _FAILOVER_THRESHOLD
        usage    = get_today_usage()
        failover = is_failover_active()

        pct = int(usage / _DAILY_LIMIT * 100)
        print(f"[health_check] Groq TPD usage: {usage:,}/{_DAILY_LIMIT:,} tokens ({pct}%) — failover: {failover}")

        if failover:
            _send_telegram(
                f"⚠️ <b>Groq Quota Warning — {date}</b>\n\n"
                f"Daily token budget at <b>{usage:,}/{_DAILY_LIMIT:,}</b> ({pct}%).\n"
                f"Remaining 'fast' calls automatically routed to <b>gpt-4o-mini</b> "
                f"(no analysis degradation — cost impact: ~$0.01 extra).\n\n"
                f"<i>No action needed. Failover is automatic.</i>\n"
                f'📊 <a href="{_DASHBOARD}">Live Dashboard</a>'
            )
    except Exception as e:
        print(f"[health_check] check_groq_quota error (non-fatal): {e}")


def check_pipeline_errors(all_results: dict, date: str) -> None:
    """
    After _analyze_all() runs, scan for agent errors across all tickers.
    Correlates failures by ticker × agent and detects Groq TPD exhaustion
    (RateLimitError pattern) to distinguish budget issues from real bugs.
    Sends a Telegram alert if errors are widespread (>1 ticker per agent).
    """
    # Collect per-ticker failures: {ticker: [agent, ...]}
    ticker_failures: dict[str, list[str]] = {}
    agent_fail_counts: dict[str, int] = {}
    rate_limit_errors = 0
    total_tickers = 0

    for ticker, state in all_results.items():
        if not isinstance(state, dict):
            continue
        errors = state.get("errors", [])
        if not errors:
            continue
        total_tickers += 1
        for err_entry in errors:
            agent = err_entry.get("agent", "unknown")
            err_text = str(err_entry.get("error", "")).lower()
            ticker_failures.setdefault(ticker, []).append(agent)
            agent_fail_counts[agent] = agent_fail_counts.get(agent, 0) + 1
            if "429" in err_text or "rate limit" in err_text or "ratelimit" in err_text or "tpd" in err_text:
                rate_limit_errors += 1

    widespread = {a: n for a, n in agent_fail_counts.items() if n > 1}
    if not widespread or not total_tickers:
        return

    if not _TELEGRAM_TOKEN or not _TELEGRAM_CHAT:
        return

    # Detect if this looks like TPD exhaustion:
    # many tickers fail across multiple agents simultaneously at end of ticker list
    is_quota_exhaustion = rate_limit_errors >= 3 or (
        len(ticker_failures) >= 3 and len(widespread) >= 3
    )

    if is_quota_exhaustion:
        # Build a clear, specific message
        affected = sorted(ticker_failures.keys())
        lines = [
            f"⚠️ <b>Pipeline Health Warning — {date}</b>\n",
            f"Groq TPD (token/day) budget exhausted mid-run.\n",
            f"Affected tickers: <b>{', '.join(affected)}</b>",
            f"Failed agents per ticker:",
        ]
        for tkr, agents in sorted(ticker_failures.items()):
            lines.append(f"  • <b>{tkr}</b>: {', '.join(agents)}")
        lines.append(
            f"\n<b>Root cause:</b> Groq llama-3.3-70b hit the 100k TPD limit mid-session. "
            f"Failover to gpt-4o-mini is now active for the rest of today."
        )
    else:
        lines = [
            f"⚠️ <b>Pipeline Health Warning — {date}</b>\n",
            "Agent errors detected across multiple tickers:\n",
        ]
        for agent, count in widespread.items():
            tickers_hit = [t for t, agents in ticker_failures.items() if agent in agents]
            lines.append(f"  • <b>{agent}</b>: failed on {count} ticker(s) — {', '.join(tickers_hit)}")

    lines.append(
        "\n<i>Check Actions logs for full error details.</i>"
        f'\n\n📊 <a href="{_DASHBOARD}">Live Dashboard</a>'
    )

    _send_telegram("\n".join(lines))
    print(f"[health_check] Pipeline error alert sent — {widespread}")
