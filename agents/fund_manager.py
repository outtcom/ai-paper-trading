"""
Fund Manager Agent
Model: claude-opus-4-6 (via LiteLLM) — final decision gate, non-negotiable
Reviews the full analysis chain and risk-adjusted decision to produce
the final executable order. This is the last gate before order submission.
"""
from typing import Dict, Optional
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm
from config import MODELS, INITIAL_CAPITAL
from tools.state_manager import save_state, write_log, log_error

SYSTEM_PROMPT = """You are the fund manager at a trading firm.
You are the final decision maker before any order is submitted.
Review the full analysis chain and risk-adjusted recommendation.

Your responsibilities:
- Confirm or override the risk-adjusted decision
- Determine the exact share quantity to buy/sell based on portfolio state
- Set the final stop-loss level
- Ensure no single position exceeds portfolio risk limits

Output a JSON object with:
{
  "action": "buy" | "sell" | "hold",
  "ticker": "<ticker>",
  "qty": int (number of shares, 0 if hold),
  "position_size_pct": float (fraction of portfolio),
  "stop_loss_pct": float or null,
  "override": false | "reduced" | "rejected",
  "final_reasoning": "2-3 sentence explanation of final decision"
}

Output ONLY the JSON, no other text.
CRITICAL: If action is hold, set qty to 0."""


def run(state: dict, portfolio: Optional[Dict] = None) -> dict:
    """
    Run fund manager and produce the final executable order.
    portfolio: optional dict with cash/equity/positions from paper_broker.get_portfolio()
    """
    ticker = state["ticker"]
    date = state["date"]

    try:
        risk_decision = state.get("risk_adjusted_decision", {})
        trader_decision = state.get("trader_decision", {})

        # Use provided portfolio or estimate from initial capital
        if portfolio is None:
            portfolio = {"cash": INITIAL_CAPITAL, "equity": INITIAL_CAPITAL, "positions": []}

        context = f"""Ticker: {ticker} | Date: {date}

Portfolio State:
{json.dumps(portfolio, indent=2)}

Trader's Decision:
{json.dumps(trader_decision, indent=2)}

Risk-Adjusted Decision (from risk management team):
{json.dumps(risk_decision, indent=2)}

Summary of Analysis:
- Fundamental: {state.get('fundamental_report', 'N/A')[:300]}...
- Sentiment: {state.get('sentiment_report', 'N/A')[:200]}...
- Technical: {state.get('technical_report', 'N/A')[:200]}...

Make the final order decision for {ticker}."""

        response = litellm.completion(
            model=MODELS["decision"],
            max_tokens=400,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ],
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        final_order = json.loads(raw)
        final_order["ticker"] = ticker  # ensure ticker is set

        state["final_order"] = final_order
        write_log(ticker, date, f"[FUND MANAGER - FINAL ORDER]\n{json.dumps(final_order, indent=2)}")
        save_state(state)

    except Exception as e:
        state = log_error(state, "fund_manager", str(e))
        state["final_order"] = {
            "action": "hold",
            "ticker": ticker,
            "qty": 0,
            "position_size_pct": 0,
            "stop_loss_pct": None,
            "override": False,
            "final_reasoning": f"Fund manager error — defaulting to HOLD: {e}",
        }

    return state
