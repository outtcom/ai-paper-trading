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
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm
from config import MODELS, INITIAL_CAPITAL, INDEX_ETFS, ETF_MAX_ALLOCATION_PCT
from tools.state_manager import save_state, write_log, log_error

SYSTEM_PROMPT = """You are the fund manager at a trading firm.
You are the final decision maker before any order is submitted.
Review the full analysis chain, risk-adjusted recommendation, and today's strategy brief.

Your responsibilities:
- Confirm or override the risk-adjusted decision
- Determine the exact share quantity to buy/sell based on portfolio state
- Set the final stop-loss level
- Ensure no single position exceeds portfolio risk limits

IMPORTANT: When the strategy brief shows risk-on posture or high capital deployment
priority, avoid reducing position sizes without a specific risk reason. The cost of
under-deployment is real — it guarantees underperforming the benchmark.

CRITICAL RULE: The benchmark is SPY. A HOLD decision is NOT neutral — it is an active
bet that cash outperforms equities. In a bull market, cash always loses.

DECISION TREE (non-negotiable — follow exactly in order):

STEP 0 — Upstream failure check (HIGHEST PRIORITY — overrides all other steps):
  → If trader_decision._failed is True → output HOLD immediately. Do NOT proceed further.
    The Trader agent experienced an API or parsing error. There is no analysis to act on.
    Executing without Trader analysis is irresponsible regardless of deployment priority.

STEP 1 — Check rejection conditions. If ANY of these apply → output HOLD immediately:
  1. VIX ≥ 25 AND explicitly flagged as elevated in the strategy brief
  2. Adding this position would push portfolio beta above the maximum limit
  3. Ticker is in an "avoid" sector listed in the strategy brief
  4. Earnings are within 3 days for this ticker (mentioned in the analysis)
  5. Risk management team's consolidated risk_assessment is "rejected" (majority of the 3 perspectives voted reject) — this is an absolute veto regardless of market posture or capital deployment priority

STEP 2 — If no rejection condition applies, use this logic:
  (STEP 0 takes absolute precedence over the rules below — even over capital deployment urgency.)
  → market_posture "risk-on"  OR  capital_deployment_priority "HIGH" or "CRITICAL" → BUY. This is mandatory.
  → market_posture "neutral"  AND  trader conviction ≥ "medium"                    → BUY.
  → market_posture "risk-off" AND  capital_deployment_priority "HIGH"               → BUY (urgency overrides caution).
  → market_posture "risk-off" AND  capital_deployment_priority "normal"             → HOLD.

IMPORTANT: risk_budget_multiplier from the strategy brief controls SIZING, not whether to BUY.
A reduced multiplier means buy a smaller position — it does NOT mean HOLD.
Reminder: HOLD is an active bet that cash beats equities. In a risk-on market, that bet loses every time.

Output a JSON object with:
{
  "action": "buy" | "sell" | "hold",
  "ticker": "<ticker>",
  "qty": int (number of shares, 0 if hold),
  "position_size_pct": float (fraction of portfolio),
  "stop_loss_pct": float or null,
  "override": false | "reduced" | "rejected",
  "final_reasoning": "<specific 3-5 sentence explanation — MUST include: the primary signal driving the decision, one concrete number from the analysis (price level, P/E, RSI, etc.), what the risk team flagged and whether it changes the view, and the key asymmetry (upside vs downside in concrete terms)>"
}

Rules for final_reasoning — it will be read by the portfolio owner on their phone:
- State the specific entry thesis in one sentence (not 'positive outlook' — say what exactly signals go)
- Cite at least one real data point (e.g. "NVDA trading at $875, broke above 50d MA at $845")
- State what the risk manager said and whether you agree or disagree and why
- State the specific upside target and downside risk in dollar or % terms
- If you override the risk decision, explain the exact reason with a specific data point

Output ONLY the JSON, no other text.
CRITICAL: If action is hold, set qty to 0."""


def run(state: dict, portfolio: Optional[Dict] = None, strategy_brief: Optional[Dict] = None) -> dict:
    """
    Run fund manager and produce the final executable order.
    portfolio: optional dict with cash/equity/positions from paper_broker.get_portfolio()
    strategy_brief: optional daily strategy brief from strategy_consultant agent
    """
    ticker = state["ticker"]
    date = state["date"]

    try:
        risk_decision = state.get("risk_adjusted_decision", {})
        trader_decision = state.get("trader_decision", {})

        # Use provided portfolio or estimate from initial capital
        if portfolio is None:
            portfolio = {"cash": INITIAL_CAPITAL, "equity": INITIAL_CAPITAL, "positions": {}}

        # Build strategy brief context if provided
        brief_context = ""
        if strategy_brief:
            brief_context = f"""
TODAY'S STRATEGY BRIEF:
- Market posture: {strategy_brief.get('market_posture', 'neutral').upper()}
- Primary thesis: {strategy_brief.get('primary_thesis', 'N/A')}
- Focus sectors: {', '.join(strategy_brief.get('focus_sectors', [])) or 'None specified'}
- Avoid sectors: {', '.join(strategy_brief.get('avoid_sectors', [])) or 'None specified'}
- Capital deployment priority: {strategy_brief.get('capital_deployment_priority', 'normal').upper()}
- Conviction floor: {strategy_brief.get('conviction_floor', 'medium')}
"""

        context = f"""Ticker: {ticker} | Date: {date}
{brief_context}
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

        # Retry up to 2 times — Opus occasionally returns empty content under load
        raw = ""
        for attempt in range(3):
            response = litellm.completion(
                model=MODELS["decision"],
                max_tokens=700,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
            )
            raw = (response.choices[0].message.content or "").strip()
            if raw:
                break
            if attempt < 2:
                time.sleep(3)

        if not raw:
            raise ValueError("Fund manager returned empty response after 3 attempts")

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        final_order = json.loads(raw)
        final_order["ticker"] = ticker  # ensure ticker is set

        # Hard block: risk-team majority-reject is an absolute veto, independent of
        # deployment urgency. Reduced size is allowed through; a full reject is not.
        if final_order.get("action") == "buy" and risk_decision.get("risk_assessment") == "rejected":
            final_order["action"] = "hold"
            final_order["qty"] = 0
            final_order["override"] = "rejected"
            final_order["final_reasoning"] = (
                "Risk team majority-rejected this trade — hard-blocked regardless of "
                "deployment posture. " + (final_order.get("final_reasoning") or "")
            )

        # Enforce ETF allocation cap (max 40% of portfolio equity in ETFs combined)
        if ticker in INDEX_ETFS and final_order.get("action") == "buy":
            portfolio_equity = portfolio.get("equity", INITIAL_CAPITAL)
            raw_positions = portfolio.get("positions", {})
            positions_dict = raw_positions if isinstance(raw_positions, dict) else {}
            etf_positions = {k: v for k, v in positions_dict.items() if k in INDEX_ETFS}
            current_etf_exposure = sum(
                v.get("cost_basis", 0)
                for v in etf_positions.values()
                if isinstance(v, dict)
            )
            proposed_usd = portfolio_equity * float(final_order.get("position_size_pct") or 0.25)
            if portfolio_equity > 0 and (current_etf_exposure + proposed_usd) / portfolio_equity > ETF_MAX_ALLOCATION_PCT:
                # Scale down to fit within cap
                headroom_pct = max(0.0, ETF_MAX_ALLOCATION_PCT - current_etf_exposure / portfolio_equity)
                if headroom_pct < 0.03:
                    # Less than 3% headroom — not worth a position
                    final_order["action"] = "hold"
                    final_order["qty"] = 0
                    final_order["final_reasoning"] = (
                        f"ETF allocation cap: current ETF exposure {current_etf_exposure/portfolio_equity*100:.1f}% "
                        f"+ proposed would exceed {ETF_MAX_ALLOCATION_PCT*100:.0f}% cap. Holding."
                    )
                else:
                    final_order["position_size_pct"] = round(headroom_pct, 4)
                    final_order["final_reasoning"] = (
                        f"ETF cap applied: position_size_pct trimmed to {headroom_pct*100:.1f}% "
                        f"(ETF cap {ETF_MAX_ALLOCATION_PCT*100:.0f}%). " + (final_order.get("final_reasoning") or "")
                    )

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
