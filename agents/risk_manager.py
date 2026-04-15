"""
Risk Management Agent
Model: groq/llama-3.1-70b-versatile (via LiteLLM)
Reviews the trader's decision from three risk perspectives
(risk-seeking, neutral, risk-conservative) and produces a risk-adjusted decision.
"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm
from config import MODELS, RISK_PERSPECTIVES
from tools.state_manager import save_state, write_log, log_error

PERSPECTIVES = {
    "risk_seeking": "You focus on maximizing return potential. You push for larger positions if the thesis is strong.",
    "neutral": "You balance risk and reward evenly. You look for a fair position size given the evidence.",
    "risk_conservative": "You prioritize downside protection above all else. You reduce positions if any significant risk exists.",
}

SYSTEM_PROMPT = """You are a risk manager at a trading firm.
You will review a trader's proposed decision from a specific risk perspective.
Your job is to evaluate whether the position size and action are appropriate given the risk.

Output a JSON object with:
{{
  "perspective": "risk_seeking" | "neutral" | "risk_conservative",
  "assessment": "approve" | "reduce" | "reject",
  "recommended_position_size": float (0.0 to 0.15),
  "rationale": "1-2 sentence explanation"
}}

Output ONLY the JSON, no other text."""

FACILITATOR_SYSTEM = """You are a risk management facilitator at a trading firm.
You have received three risk perspectives on a proposed trade.
Your job is to synthesize them into one final risk-adjusted decision.

Output a JSON object with:
{{
  "action": "buy" | "sell" | "hold",
  "final_position_size": float,
  "stop_loss_pct": float or null,
  "risk_assessment": "approved" | "reduced" | "rejected",
  "reasoning": "2-3 sentence synthesis"
}}

Output ONLY the JSON, no other text."""


def run(state: dict) -> dict:
    """Run risk management team and produce risk-adjusted decision."""
    ticker = state["ticker"]
    date = state["date"]

    try:
        trader_decision = state.get("trader_decision", {})
        context = f"""Ticker: {ticker} | Date: {date}

Trader's Proposed Decision:
{json.dumps(trader_decision, indent=2)}

Bull Case Summary:
{state.get('bull_case', 'N/A')[:500]}

Bear Case Summary:
{state.get('bear_case', 'N/A')[:500]}"""

        perspective_outputs = []

        for perspective in RISK_PERSPECTIVES:
            perspective_system = SYSTEM_PROMPT + f"\n\nYour perspective: {perspective}. {PERSPECTIVES[perspective]}"
            response = litellm.completion(
                model=MODELS["fast"],
                max_tokens=250,
                messages=[
                    {"role": "system", "content": perspective_system},
                    {"role": "user", "content": f"{context}\n\nEvaluate from the {perspective} perspective."},
                ],
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            perspective_outputs.append(json.loads(raw))

        # Facilitator synthesizes the three perspectives
        facilitator_input = f"""{context}

Three Risk Perspectives:
{json.dumps(perspective_outputs, indent=2)}

Synthesize into one final risk-adjusted decision."""

        facilitator_response = litellm.completion(
            model=MODELS["fast"],
            max_tokens=300,
            messages=[
                {"role": "system", "content": FACILITATOR_SYSTEM},
                {"role": "user", "content": facilitator_input},
            ],
        )
        raw = facilitator_response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        risk_decision = json.loads(raw)
        risk_decision["perspectives"] = perspective_outputs

        state["risk_adjusted_decision"] = risk_decision
        write_log(ticker, date, f"[RISK MANAGEMENT]\nPerspectives: {json.dumps(perspective_outputs, indent=2)}\nFinal: {json.dumps(risk_decision, indent=2)}")
        save_state(state)

    except Exception as e:
        state = log_error(state, "risk_manager", str(e))
        trader_dec = state.get("trader_decision", {})
        state["risk_adjusted_decision"] = {
            "action": trader_dec.get("action", "hold"),
            "final_position_size": trader_dec.get("position_size", 0.05),
            "stop_loss_pct": trader_dec.get("stop_loss_pct"),
            "risk_assessment": "approved",
            "reasoning": f"Risk manager error — using trader decision as fallback: {e}",
        }

    return state
