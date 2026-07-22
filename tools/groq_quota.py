"""
Daily Groq token-quota tracker.

Groq free tier: 100,000 tokens/day (TPD) on llama-3.3-70b-versatile.
Tracks cumulative usage across all workflows sharing the same GROQ_API_KEY.
Stored in .tmp/state/groq_quota_YYYY-MM-DD.json so multiple processes share state.

Once usage crosses FAILOVER_THRESHOLD (85k), get_effective_fast_model() automatically
returns gpt-4o-mini instead of Groq — graceful degradation rather than hard failure.

Usage:
    from tools.groq_quota import track_tokens, get_effective_fast_model, get_today_usage
"""
import json
import os
from datetime import datetime, timezone

_QUOTA_DIR          = os.path.join(".tmp", "state")
_DAILY_LIMIT        = 100_000
_FAILOVER_THRESHOLD = 85_000    # switch providers at 85% of daily cap
_FAILOVER_MODEL     = "openai/gpt-4o-mini"   # already wired in config.py as "debate"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _path(date: str = None) -> str:
    return os.path.join(_QUOTA_DIR, f"groq_quota_{date or _today()}.json")


def _load(date: str = None) -> dict:
    path = _path(date)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "date":             date or _today(),
        "total_tokens":     0,
        "calls":            0,
        "failover_active":  False,
        "failover_started_at": None,
    }


def _save(data: dict) -> None:
    os.makedirs(_QUOTA_DIR, exist_ok=True)
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(_path(data["date"]), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def track_tokens(response) -> int:
    """
    Extract total_tokens from a litellm response and add to today's counter.
    Activates failover mode when the threshold is crossed (idempotent).
    Returns the new cumulative total for today.
    """
    try:
        used = 0
        if hasattr(response, "usage") and response.usage:
            used = getattr(response.usage, "total_tokens", 0) or 0
        if not used:
            return get_today_usage()

        today = _today()
        data  = _load(today)
        data["total_tokens"] += used
        data["calls"]        += 1

        if data["total_tokens"] >= _FAILOVER_THRESHOLD and not data["failover_active"]:
            data["failover_active"]      = True
            data["failover_started_at"]  = datetime.now(timezone.utc).isoformat()
            print(
                f"[groq_quota] ⚠️  Threshold reached ({data['total_tokens']:,}/{_DAILY_LIMIT:,} TPD) "
                f"— switching remaining 'fast' calls to {_FAILOVER_MODEL}"
            )

        _save(data)
        return data["total_tokens"]
    except Exception as e:
        print(f"[groq_quota] track_tokens error (non-fatal): {e}")
        return 0


def get_today_usage() -> int:
    """Return cumulative tokens used today (0 if counter file not yet created)."""
    return _load(_today()).get("total_tokens", 0)


def is_failover_active() -> bool:
    """Return True if today's Groq token usage has crossed the failover threshold."""
    return _load(_today()).get("failover_active", False)


def get_effective_fast_model() -> str:
    """
    Return the correct model for 'fast' (Groq) calls.
    Returns the failover model (gpt-4o-mini) once quota is near exhaustion.
    """
    from config import MODELS
    if is_failover_active():
        return _FAILOVER_MODEL
    return MODELS["fast"]
