"""
Risk Management Agent
Model: groq/llama-3.3-70b-versatile (via LiteLLM), quota-aware failover to gpt-4o-mini.

Evaluates the trader's proposed decision from three risk perspectives simultaneously
in ONE LLM call, then applies majority-rule synthesis inline — cutting per-ticker
Groq call volume from 4 down to 1 (~43% reduction in daily token usage).
"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm
litellm.num_retries = 3
from tools.state_manager import save_state, write_log, log_error
from tools.groq_quota import track_tokens, get_effective_fast_model

SYSTEM_PROMPT = """You are a risk management team at a trading firm.
Evaluate the proposed trade from THREE distinct perspectives simultaneously,
then apply majority-rule to produce one final synthesized decision.

PERSPECTIVE DEFINITIONS:
- risk_seeking:      Maximize return potential. Push for larger positions when thesis is strong.
- neutral:           Balance risk and reward evenly. Size fairly given the evidence.
- risk_conservative: Prioritize downside protection. Reduce positions if any significant risk exists.

MAJORITY RULE (apply before anything else):
- Count how many of the 3 perspectives say "approve", "reduce", or "reject"
- 2+ say "approve"  → risk_assessment = "approved"
- 2+ say "reject"   → risk_assessment = "rejected"
- No clear majority (split) → defer to the "neutral" perspective's assessment
- Do NOT default to "reduced" simply because risk_conservative prefers it
- final_position_size MUST be ≥ the median of the 3 recommended sizes, not the minimum

Output ONLY a JSON object with no other text:
{
  "perspectives": [
    {"perspective": "risk_seeking",      "assessment": "approve|reduce|reject", "recommended_position_size": 0.00},
    {"perspective": "neutral",           "assessment": "approve|reduce|reject", "recommended_position_size": 0.00},
    {"perspective": "risk_conservative", "assessment": "approve|reduce|reject", "recommended_position_size": 0.00}
  ],
  "action": "buy|sell|hold",
  "final_position_size": 0.00,
  "stop_loss_pct": null,
  "risk_assessment": "approved|reduced|rejected",
  "reasoning": "2-3 sentence synthesis"
}"""


def run(state: dict) -> dict:
    """Run risk management team (single Groq call) and produce risk-adjusted decision."""
    ticker = state["ticker"]
    date   = state["date"]

    try:
        trader_decision = state.get("trader_decision", {})
        user_content = (
            f"Ticker: {ticker} | Date: {date}\n\n"
            f"Trader's Proposed Decision:\n{json.dumps(trader_decision, indent=2)}\n\n"
            f"Bull Case Summary:\n{state.get('bull_case', 'N/A')[:500]}\n\n"
            f"Bear Case Summary:\n{state.get('bear_case', 'N/A')[:500]}\n\n"
            "Evaluate from all three risk perspectives and produce the synthesized decision."
        )

        model    = get_effective_fast_model()
        response = litellm.completion(
            model    = model,
            max_tokens = 400,
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_content},
            ],
        )
        track_tokens(response)

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        risk_decision = json.loads(raw)

        state["risk_adjusted_decision"] = risk_decision
        write_log(ticker, date, f"[RISK MANAGEMENT]\nModel: {model}\n{json.dumps(risk_decision, indent=2)}")
        save_state(state)

    except Exception as e:
        state = log_error(state, "risk_manager", str(e))
        trader_dec = state.get("trader_decision", {})
        state["risk_adjusted_decision"] = {
            "action":              trader_dec.get("action", "hold"),
            "final_position_size": trader_dec.get("position_size", 0.05),
            "stop_loss_pct":       trader_dec.get("stop_loss_pct"),
            "risk_assessment":     "approved",
            "reasoning":           f"Risk manager error — using trader decision as fallback: {e}",
        }

    return state
