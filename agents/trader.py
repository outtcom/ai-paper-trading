"""
Trader Agent
Model: claude-sonnet-4-6 (via LiteLLM)
Synthesizes all analyst reports and the bull/bear debate into a trading decision.
Runs 3 risk-profile variants (aggressive, moderate, conservative) and picks
based on configured DEFAULT_RISK_PROFILE.
"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm
from config import MODELS, DEFAULT_RISK_PROFILE, MAX_POSITION_SIZE
from tools.state_manager import save_state, write_log, log_error

RISK_PROFILES = {
    "aggressive": {
        "description": "You accept higher risk for potentially higher returns. You act on strong signals even with some uncertainty.",
        "max_position": 0.15,
    },
    "moderate": {
        "description": "You balance risk and reward. You act on clear signals with reasonable conviction.",
        "max_position": 0.10,
    },
    "conservative": {
        "description": "You prioritize capital preservation. You only act on high-conviction signals with strong confluence.",
        "max_position": 0.05,
    },
}

SYSTEM_TEMPLATE = """You are a {profile} trader at a trading firm.
{description}

You have received analysis from fundamental, sentiment, and technical analysts,
plus a bull/bear debate. Synthesize this into a trading decision.

Your output must be a JSON object with this exact structure:
{{
  "action": "buy" | "sell" | "hold",
  "conviction": "low" | "medium" | "high",
  "position_size": 0.0-{max_position} (fraction of portfolio),
  "entry_price_target": null or float,
  "stop_loss_pct": null or float (e.g. 0.05 for 5%),
  "reasoning": "2-3 sentence explanation"
}}

Output ONLY the JSON, no other text."""


def run(state: dict) -> dict:
    """Run trader agent with the configured risk profile."""
    ticker = state["ticker"]
    date = state["date"]
    profile = DEFAULT_RISK_PROFILE

    try:
        profile_config = RISK_PROFILES[profile]
        system = SYSTEM_TEMPLATE.format(
            profile=profile,
            description=profile_config["description"],
            max_position=profile_config["max_position"],
        )

        context = f"""Ticker: {ticker} | Date: {date}

FUNDAMENTAL ANALYSIS:
{state.get('fundamental_report', 'N/A')}

SENTIMENT ANALYSIS:
{state.get('sentiment_report', 'N/A')}

TECHNICAL ANALYSIS:
{state.get('technical_report', 'N/A')}

BULL CASE:
{state.get('bull_case', 'N/A')}

BEAR CASE:
{state.get('bear_case', 'N/A')}

Based on all of the above, what is your trading decision for {ticker}?"""

        response = litellm.completion(
            model=MODELS["analyst"],
            max_tokens=400,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": context},
            ],
        )

        raw = response.choices[0].message.content.strip()
        # Strip markdown code blocks if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        decision = json.loads(raw)
        decision["risk_profile"] = profile

        state["trader_decision"] = decision
        write_log(ticker, date, f"[TRADER ({profile.upper()})]\n{json.dumps(decision, indent=2)}")
        save_state(state)

    except Exception as e:
        state = log_error(state, "trader", str(e))
        state["trader_decision"] = {"action": "hold", "reasoning": f"Trader error: {e}", "risk_profile": profile}

    return state
