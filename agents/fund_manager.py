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
            portfolio = {"cash": INITIAL_CAPITAL, "equity": INITIAL_CAPITAL, "positions": []}

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

        # Enforce ETF allocation cap (max 40% of portfolio equity in ETFs combined)
        if ticker in INDEX_ETFS and final_order.get("action") == "buy":
            portfolio_equity = portfolio.get("equity", INITIAL_CAPITAL)
            etf_positions = {
                k: v for k, v in portfolio.get("positions", {}).items()
                if isinstance(portfolio.get("positions", {}), dict) and k in INDEX_ETFS
            }
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
