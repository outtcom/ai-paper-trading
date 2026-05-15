"""
Performance Analyst — Session post-mortem agent.
Reviews the completed session's trade history, equity curve, and deployment metrics
to identify causes of underperformance vs SPY and output concrete recommendations.
"""
import json
import os
from datetime import datetime

import litellm
from dotenv import load_dotenv

load_dotenv()

_MODEL = "claude-sonnet-4-6"

_SYSTEM_PROMPT = """You are a quantitative performance analyst reviewing a paper trading session.

Your job is to identify the specific causes of underperformance vs SPY and output concrete
parameter recommendations with quantified expected impact.

Focus on:
- Capital deployment rate (average % deployed across the session)
- Position sizing relative to available capital
- Hold/win/loss patterns — did the fund manager HOLD too much?
- Day-trade vs swing trade return contribution
- Scalping signal count and P&L
- Sector concentration or missed sector rotation

Output ONLY valid JSON in this exact format:
{
  "session_alpha": <float: our_return_pct - spy_return_pct>,
  "deployment_avg_pct": <float: average capital deployed across session>,
  "primary_failures": ["ranked list of what caused underperformance"],
  "primary_successes": ["what worked well — keep these"],
  "parameter_recommendations": [
    {"param": "...", "current": <value>, "recommended": <value>, "reason": "..."}
  ],
  "strategy_recommendations": ["2-3 tactical changes for the next session"],
  "session2_targets": {
    "min_alpha_vs_spy": <float>,
    "target_deployment_pct": <float>,
    "target_win_rate": <float>
  }
}

Do not include any text outside the JSON block. Do not explain. Output JSON only."""


def run(portfolio: dict) -> dict:
    """
    Analyse a completed session portfolio and return structured recommendations.

    Args:
        portfolio: The full portfolio.json dict from the completed session.

    Returns:
        Dict with analysis results (matches the JSON schema in the system prompt).
    """
    session = portfolio.get("session", {})
    stats   = portfolio.get("stats", {})
    equity  = portfolio.get("equity", 5000.0)
    initial = portfolio.get("initial_capital", 5000.0)

    our_return_pct = round((equity - initial) / initial * 100, 2)
    spy_return_pct = round(stats.get("benchmark_return_pct", 0) or 0, 2)
    alpha_pct      = round(our_return_pct - spy_return_pct, 2)

    # Build a rich context string for the LLM
    trade_history = portfolio.get("trade_history", [])
    equity_curve  = portfolio.get("equity_curve", [])
    scalping_data = portfolio.get("scalping_capital", {})
    dt_data       = portfolio.get("day_trade_capital", {})

    wins  = [t for t in trade_history if t.get("pnl", 0) > 0]
    losses = [t for t in trade_history if t.get("pnl", 0) < 0]
    holds_skipped = session.get("holds_skipped", 0)

    context = f"""SESSION SUMMARY
Session: Day {session.get('current_day', '?')} of {session.get('total_days', '?')}
Our return:   {our_return_pct:+.2f}%
SPY return:   {spy_return_pct:+.2f}%
Alpha:        {alpha_pct:+.2f}%
Final equity: ${equity:,.2f}  (started: ${initial:,.2f})

RISK METRICS
Sharpe:        {stats.get('sharpe_ratio', 'N/A')}
Sortino:       {stats.get('sortino_ratio', 'N/A')}
Max drawdown:  {stats.get('max_drawdown_pct', 'N/A')}%
Win rate:      {stats.get('win_rate', 'N/A')}%
Calmar:        {stats.get('calmar_ratio', 'N/A')}

TRADE STATISTICS
Total closed trades:  {len(trade_history)}
Wins:   {len(wins)}  |  Losses: {len(losses)}
Avg win P&L:   ${sum(t.get('pnl',0) for t in wins) / max(len(wins),1):.2f}
Avg loss P&L:  ${sum(t.get('pnl',0) for t in losses) / max(len(losses),1):.2f}
Holds/skipped by fund manager: {holds_skipped}

SCALPING POOL
Initial: ${scalping_data.get('initial', 5000):.2f}
Current: ${scalping_data.get('equity', 5000):.2f}
Return:  {round((scalping_data.get('equity', 5000) - scalping_data.get('initial', 5000)) / scalping_data.get('initial', 5000) * 100, 2):+.2f}%
Signals fired: {scalping_data.get('signals_fired', 0)}

DAY TRADE POOL
Initial: ${dt_data.get('initial', 5000):.2f}
Current: ${dt_data.get('equity', 5000):.2f}
Return:  {round((dt_data.get('equity', 5000) - dt_data.get('initial', 5000)) / dt_data.get('initial', 5000) * 100, 2):+.2f}%

EQUITY CURVE (last 10 snapshots)
{json.dumps(equity_curve[-10:], indent=2) if equity_curve else "No equity curve data"}

RECENT TRADE HISTORY (last 15 trades)
{json.dumps(trade_history[-15:], indent=2) if trade_history else "No trades recorded"}
"""

    response = litellm.completion(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": context},
        ],
        temperature=0.1,
        max_tokens=1500,
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {
            "session_alpha": alpha_pct,
            "deployment_avg_pct": None,
            "primary_failures": ["Unable to parse LLM output"],
            "primary_successes": [],
            "parameter_recommendations": [],
            "strategy_recommendations": [],
            "session2_targets": {
                "min_alpha_vs_spy": 2.0,
                "target_deployment_pct": 70.0,
                "target_win_rate": 0.55,
            },
            "_raw": raw,
        }

    return result


def format_telegram_message(review: dict, session_num: int = 1) -> str:
    """Format the performance review as a Telegram HTML message."""
    alpha = review.get("session_alpha", 0)
    alpha_str = f"{alpha:+.2f}%" if isinstance(alpha, (int, float)) else str(alpha)
    depl  = review.get("deployment_avg_pct")
    depl_str = f"{depl:.1f}%" if isinstance(depl, (int, float)) else "N/A"

    failures    = review.get("primary_failures", [])
    successes   = review.get("primary_successes", [])
    recs        = review.get("strategy_recommendations", [])
    targets     = review.get("session2_targets", {})
    param_recs  = review.get("parameter_recommendations", [])

    lines = [
        f"📊 <b>Session {session_num} Performance Review</b>\n",
        f"Alpha vs SPY:     <b>{alpha_str}</b>",
        f"Avg deployed:     {depl_str}",
        "",
        "❌ <b>Primary failures:</b>",
    ]
    for i, f in enumerate(failures[:5], 1):
        lines.append(f"  {i}. {f}")

    lines += ["", "✅ <b>What worked:</b>"]
    for s in successes[:3]:
        lines.append(f"  • {s}")

    if param_recs:
        lines += ["", "⚙️ <b>Parameter changes:</b>"]
        for p in param_recs[:5]:
            lines.append(f"  {p['param']}: {p['current']} → {p['recommended']}  ({p['reason']})")

    if recs:
        lines += ["", "🎯 <b>Session 2 tactics:</b>"]
        for r in recs[:3]:
            lines.append(f"  • {r}")

    if targets:
        lines += [
            "",
            f"🏁 <b>Session 2 targets:</b>",
            f"  Alpha: ≥{targets.get('min_alpha_vs_spy', 2):.1f}%  |  "
            f"Deployed: ≥{targets.get('target_deployment_pct', 70):.0f}%  |  "
            f"Win rate: ≥{targets.get('target_win_rate', 0.55)*100:.0f}%",
        ]

    return "\n".join(lines)
