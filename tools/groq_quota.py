"""
Daily Groq token-quota tracker with automatic failover.

Groq free tier: 100,000 tokens/day (TPD) on llama-3.3-70b-versatile.
Tracks cumulative usage across all workflows sharing the same GROQ_API_KEY.
Stored in .tmp/state/groq_quota_YYYY-MM-DD.json so multiple processes share state.

Two-layer failover protection:
  1. Proactive (counter-based): at 85k tokens, get_effective_fast_model()
     returns gpt-4o-mini for all remaining calls that day.
  2. Reactive (error-based): groq_completion() catches 429/RateLimitError on
     the first failed Groq call, marks failover active, and immediately retries
     the same request with gpt-4o-mini — zero calls dropped.

Usage:
    from tools.groq_quota import groq_completion, get_today_usage
"""
import json
import os
from datetime import datetime, timezone

_QUOTA_DIR          = os.path.join(".tmp", "state")
_DAILY_LIMIT        = 100_000
_FAILOVER_THRESHOLD = 85_000    # proactive switch at 85% of daily cap
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


def _activate_failover() -> None:
    """Mark failover as active for today (idempotent)."""
    today = _today()
    data  = _load(today)
    if not data["failover_active"]:
        data["failover_active"]     = True
        data["failover_started_at"] = datetime.now(timezone.utc).isoformat()
        _save(data)
        print(f"[groq_quota] ⚠️  Failover activated — remaining 'fast' calls → {_FAILOVER_MODEL}")


def _is_rate_limit_error(exc: Exception) -> bool:
    err = str(exc).lower()
    return any(k in err for k in ("429", "rate limit", "ratelimit", "quota", "tpd", "tokens per day"))


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
            data["failover_active"]     = True
            data["failover_started_at"] = datetime.now(timezone.utc).isoformat()
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
    """Return True if failover is active for today (threshold crossed or 429 received)."""
    return _load(_today()).get("failover_active", False)


def get_effective_fast_model() -> str:
    """
    Return the correct model for 'fast' (Groq) calls.
    Returns gpt-4o-mini once quota threshold is crossed or a 429 was received.
    """
    from config import MODELS
    if is_failover_active():
        return _FAILOVER_MODEL
    return MODELS["fast"]


def groq_completion(**kwargs):
    """
    Drop-in replacement for litellm.completion() for 'fast' (Groq) calls.

    Two-layer failover:
      - Proactive: uses get_effective_fast_model() — already gpt-4o-mini if
        the counter crossed 85k on a prior call this session.
      - Reactive: if Groq returns a 429/RateLimitError, activates failover
        immediately and retries once with gpt-4o-mini — zero calls dropped.

    Automatically tracks token usage after every successful call.
    """
    import litellm
    model = get_effective_fast_model()
    kwargs["model"] = model
    # gpt-oss models are reasoning models: unset/"medium" effort can burn an
    # entire small max_tokens budget on hidden reasoning tokens before any
    # visible content is emitted (confirmed empty content, finish_reason=
    # "length" at max_tokens=400). Force low effort so the structured-JSON
    # formatter calls this function serves actually get content back.
    if "gpt-oss" in model and "reasoning_effort" not in kwargs:
        kwargs["reasoning_effort"] = "low"
    try:
        response = litellm.completion(**kwargs)
        track_tokens(response)
        return response
    except Exception as e:
        if _is_rate_limit_error(e) and model != _FAILOVER_MODEL:
            print(f"[groq_quota] Groq 429 received — activating failover and retrying with {_FAILOVER_MODEL}")
            _activate_failover()
            kwargs["model"] = _FAILOVER_MODEL
            kwargs.pop("reasoning_effort", None)  # not a gpt-oss model — param doesn't apply
            response = litellm.completion(**kwargs)
            track_tokens(response)
            return response
        raise
