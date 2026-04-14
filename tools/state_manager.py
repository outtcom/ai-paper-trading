from typing import Dict, Optional
"""
Read and write the structured communication state between agents.
Each trading cycle produces one state file per ticker per date.
State files are stored in .tmp/state/YYYY-MM-DD/<TICKER>.json
"""
import json
import os
from datetime import datetime


def _state_path(ticker: str, date: str) -> str:
    dir_path = os.path.join(".tmp", "state", date)
    os.makedirs(dir_path, exist_ok=True)
    return os.path.join(dir_path, f"{ticker}.json")


def init_state(ticker: str, date: str) -> dict:
    """Create a fresh state object for a new trading cycle."""
    state = {
        "ticker": ticker,
        "date": date,
        "created_at": datetime.utcnow().isoformat(),
        "fundamental_report": None,
        "sentiment_report": None,
        "technical_report": None,
        "bull_case": None,
        "bear_case": None,
        "trader_decision": None,
        "risk_adjusted_decision": None,
        "final_order": None,
        "errors": [],
    }
    save_state(state)
    return state


def save_state(state: dict) -> None:
    """Persist state to disk."""
    path = _state_path(state["ticker"], state["date"])
    state["updated_at"] = datetime.utcnow().isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def load_state(ticker: str, date: str) -> Optional[Dict]:
    """Load state from disk. Returns None if not found."""
    path = _state_path(ticker, date)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def log_error(state: dict, agent: str, error: str) -> dict:
    """Record an agent error into state without crashing."""
    state["errors"].append({"agent": agent, "error": error, "time": datetime.utcnow().isoformat()})
    save_state(state)
    return state


def write_log(ticker: str, date: str, content: str) -> None:
    """Write a human-readable reasoning log for debugging."""
    log_dir = os.path.join(".tmp", "logs", date)
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{ticker}.log")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n[{datetime.utcnow().isoformat()}]\n{content}\n{'='*60}\n")


if __name__ == "__main__":
    state = init_state("AAPL", "2024-01-15")
    state["fundamental_report"] = "Test fundamental report"
    save_state(state)
    loaded = load_state("AAPL", "2024-01-15")
    print(loaded)
