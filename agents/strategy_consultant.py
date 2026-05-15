"""
Strategy Consultant Agent
Model: claude-sonnet-4-6 (via LiteLLM) — runs once per day before the main pipeline.
Analyzes macro conditions and produces a daily strategy brief that guides
position sizing, sector focus, and capital deployment priority.
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm
from config import MODELS, SESSION_DAYS, INITIAL_CAPITAL, STATE_DIR

SYSTEM_PROMPT = """You are a senior portfolio strategist at a hedge fund.
Your role is to set the daily macro posture before the trading team begins analysis.

You receive market conditions, portfolio state, and session pacing context.
Your output is the strategic brief that guides every trade decision today.

Key principles:
- In a bull market with low volatility, excessive conservatism IS the risk.
  Every day sitting in cash while the S&P 500 trends up is a guaranteed loss.
- Capital deployment urgency increases as the session progresses.
  If more than halfway through the session with less than 25% deployed, increase urgency.
- Sector rotation matters: follow momentum into leading sectors.
- VIX below 18 = green light for full positioning.
- HYG above 20d MA = credit conditions healthy = favour equities.
- The PRIMARY mandate is to OUTPERFORM SPY. Negative and worsening alpha is CRITICAL —
  holding cash guarantees continued underperformance. Every 1% of uninvested capital costs
  approximately 0.5% alpha over a 30-day session.
- If alpha vs SPY is worse than -2%, override caution: risk_budget_multiplier must be ≥ 1.2
  and capital_deployment_priority must be "high" unless VIX ≥ 25 or credit is stressed.

When setting risk_budget_multiplier:
  1.3 to 1.5: Risk-on — VIX low, trend bullish, session behind on deployment
  1.0 to 1.2: Normal conditions, moderate conviction
  0.7 to 0.9: Some caution warranted
  0.5 to 0.6: Meaningful risk signals present
  Below 0.5: Only in extreme conditions (high VIX + credit stress)

Output a single JSON object — no other text:
{
  "market_posture": "risk-on" | "neutral" | "risk-off",
  "risk_budget_multiplier": float,
  "focus_sectors": ["sector_name"],
  "avoid_sectors": ["sector_name"],
  "target_new_positions": 0 | 1 | 2,
  "conviction_floor": "low" | "medium" | "high",
  "primary_thesis": "1-2 sentence strategic narrative",
  "capital_deployment_priority": "high" | "normal" | "low",
  "day_trade_go": true | false
}"""


def run(
    portfolio: dict,
    date: str,
    vix_data: dict = None,
    sector_strength: dict = None,
    hyg_data: dict = None,
) -> dict:
    """
    Run the Strategy Consultant and produce a daily strategy brief.
    Returns the strategy brief dict. On error, returns a neutral brief.
    """
    try:
        day_of_week    = datetime.strptime(date, "%Y-%m-%d").strftime("%A")
        session_info   = portfolio.get("session", {})
        session_day    = session_info.get("current_day", 1)
        total_days     = session_info.get("total_days", SESSION_DAYS)
        days_remaining = total_days - session_day
        equity         = portfolio.get("equity", INITIAL_CAPITAL)
        cash           = portfolio.get("cash", INITIAL_CAPITAL)
        deployed_pct   = round((1 - cash / equity) * 100, 1) if equity > 0 else 0
        open_positions = list(portfolio.get("positions", {}).keys())

        vix_label   = (vix_data or {}).get("label",     "VIX unknown")
        vix_roc_lbl = (vix_data or {}).get("roc_label", "VIX RoC unknown")
        spy_trend   = (vix_data or {}).get("spy_trend",  "unknown")
        hyg_label   = (hyg_data or {}).get("label",     "HYG unknown")

        if sector_strength:
            top3    = ", ".join(sector_strength.get("top_3",    [])[:3])
            bottom3 = ", ".join(sector_strength.get("bottom_3", [])[:3])
            sector_summary = f"Leaders: {top3} | Laggards: {bottom3}"
        else:
            sector_summary = "Sector data unavailable"

        expected_pct = round(session_day / total_days * 100)
        pacing_note  = "BEHIND — increase urgency" if deployed_pct < expected_pct - 15 else "on track"

        # Alpha vs SPY awareness
        session_return_pct = round((equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100, 1)
        spy_return_pct     = round(portfolio.get("stats", {}).get("benchmark_return_pct", 0) or 0, 1)
        alpha_pct          = round(session_return_pct - spy_return_pct, 1)
        alpha_label        = f"{alpha_pct:+.1f}% vs SPY"
        if alpha_pct < -2:
            alpha_urgency = "CRITICAL — widen deployment immediately"
        elif alpha_pct < 0:
            alpha_urgency = "behind — increase urgency"
        else:
            alpha_urgency = "on track"

        context = f"""DATE: {date} ({day_of_week})
SESSION PACING: Day {session_day}/{total_days} — {days_remaining} days remaining
  Expected deployment by now: ~{expected_pct}%
  Actual deployment: {deployed_pct:.0f}%
  Pacing: {pacing_note}
ALPHA vs SPY: {alpha_label} → {alpha_urgency}

PORTFOLIO:
  Equity: ${equity:,.2f}  |  Cash: ${cash:,.2f}
  Open positions: {open_positions if open_positions else "None"}

MARKET CONDITIONS:
  {vix_label}
  {vix_roc_lbl}
  SPY trend: {spy_trend}
  Credit (HYG): {hyg_label}
  Sectors: {sector_summary}

Generate today's strategy brief."""

        response = litellm.completion(
            model=MODELS["analyst"],
            max_tokens=350,
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
        brief = json.loads(raw)

        _save_brief(brief, date)
        print(f"[strategy_consultant] Posture: {brief.get('market_posture')}  "
              f"multiplier: {brief.get('risk_budget_multiplier')}×  "
              f"target positions: {brief.get('target_new_positions')}")
        return brief

    except Exception as e:
        print(f"[strategy_consultant] Error: {e} — using neutral brief")
        return _neutral_brief()


def _save_brief(brief: dict, date: str) -> None:
    try:
        state_dir = os.path.join(STATE_DIR, date)
        os.makedirs(state_dir, exist_ok=True)
        with open(os.path.join(state_dir, "strategy_brief.json"), "w") as f:
            json.dump(brief, f, indent=2)
    except Exception as e:
        print(f"[strategy_consultant] Could not save brief: {e}")


def _neutral_brief() -> dict:
    return {
        "market_posture":             "neutral",
        "risk_budget_multiplier":     1.0,
        "focus_sectors":              [],
        "avoid_sectors":              [],
        "target_new_positions":       1,
        "conviction_floor":           "medium",
        "primary_thesis":             "Neutral posture — strategy consultant unavailable.",
        "capital_deployment_priority": "normal",
        "day_trade_go":               True,
    }
