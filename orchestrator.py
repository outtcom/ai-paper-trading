"""
Orchestrator: runs the full multi-agent pipeline for one ticker on one date.
Sequence: Fundamental → Sentiment → Technical → Insider → Researcher (Bull/Bear) →
          Trader → Risk Manager → Fund Manager → Submit Order

Failover hierarchy:
  Groq agents (1-4, 7):  degrade gracefully — stub output inserted, pipeline continues.
  OpenAI agents (5):     degrade gracefully — empty researcher debate, pipeline continues.
  Anthropic agents (6):  CRITICAL — Trader failure short-circuits to "hold" for this ticker.
  Anthropic agents (8):  CRITICAL — Fund Manager failure short-circuits to "hold".
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.state_manager import init_state, write_log
from tools.paper_broker import get_portfolio, submit_order
from agents import fundamental_analyst, sentiment_analyst, technical_analyst
from agents import insider_analyst, researcher, trader, risk_manager, fund_manager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_run(state: dict, agent_name: str, agent_fn, fallback_updates: dict) -> dict:
    """
    Run an agent safely. On exception, log the error, apply fallback_updates
    to state so downstream agents have valid (if empty) inputs, and continue.
    Used for Groq and OpenAI agents — they degrade gracefully.
    """
    try:
        return agent_fn(state)
    except Exception as e:
        err_msg = f"{agent_name} failed: {type(e).__name__}: {str(e)[:300]}"
        print(f"  [!] {err_msg}")
        write_log(state["ticker"], state["date"], f"ERROR: {err_msg}")
        state.setdefault("errors", []).append({"agent": agent_name, "error": err_msg})
        for key, val in fallback_updates.items():
            if not state.get(key):
                state[key] = val
        return state


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(ticker: str, date: str, dry_run: bool = True, portfolio: dict = None, strategy_brief: dict = None) -> dict:
    """
    Run the full trading agent pipeline for one ticker.

    dry_run=True: compute decision but do NOT submit the order (safe default)
    dry_run=False: submit the order via Alpaca paper trading API
    portfolio: real session portfolio dict (cash, equity, positions)
    strategy_brief: daily strategy brief from strategy_consultant

    Returns the final state dict. On critical Anthropic failures, returns a
    "hold" state immediately without submitting any order.
    """
    print(f"\n{'='*60}")
    print(f"  TradingAgents Pipeline: {ticker} | {date}")
    print(f"{'='*60}")

    state = init_state(ticker, date)
    write_log(ticker, date, f"Pipeline started. dry_run={dry_run}")

    # ── Steps 1-4: Groq agents — degrade gracefully on failure ──────────────

    print(f"  [1/8] Fundamental Analyst...")
    state = _safe_run(state, "fundamental_analyst", fundamental_analyst.run, {
        "fundamental_report":   "Fundamental data unavailable — provider error.",
        "fundamental_analysis": {"recommendation": "NEUTRAL", "confidence": "low"},
    })

    print(f"  [2/8] Sentiment Analyst...")
    state = _safe_run(state, "sentiment_analyst", sentiment_analyst.run, {
        "sentiment_report":   "Sentiment data unavailable — provider error.",
        "sentiment_analysis": {"sentiment": "neutral", "confidence": "low"},
    })

    print(f"  [3/8] Technical Analyst...")
    state = _safe_run(state, "technical_analyst", technical_analyst.run, {
        "technical_report":   "Technical data unavailable — provider error.",
        "technical_analysis": {"signal": "neutral", "confidence": "low"},
    })

    print(f"  [4/8] Insider & Political Analyst...")
    state = _safe_run(state, "insider_analyst", insider_analyst.run, {
        "insider_report": "Insider data unavailable — provider error.",
    })

    # ── Step 5: OpenAI agent — degrade gracefully on failure ─────────────────

    print(f"  [5/8] Researcher Debate (Bull vs Bear)...")
    state = _safe_run(state, "researcher", researcher.run, {
        "bull_case": "Researcher unavailable — provider error.",
        "bear_case": "Researcher unavailable — provider error.",
        "top_news":  [],
    })

    # ── Step 6: Trader (Anthropic Sonnet) — CRITICAL ─────────────────────────
    # Failure here means no trading decision is possible for this ticker.

    print(f"  [6/8] Trader...")
    try:
        state = trader.run(state)
    except Exception as e:
        err_msg = f"Trader failed: {type(e).__name__}: {str(e)[:300]}"
        print(f"  [!] CRITICAL — {err_msg}")
        write_log(ticker, date, f"CRITICAL ERROR: {err_msg}")
        state.setdefault("errors", []).append({"agent": "trader", "error": err_msg, "critical": True})
        state["trader_decision"] = {
            "action":     "hold",
            "conviction": "none",
            "reasoning":  f"Trader agent unavailable: {err_msg}",
            "stop_loss_pct":     0.05,
            "position_size_pct": 0.0,
        }
        state["final_order"] = {
            "action":          "hold",
            "qty":             0,
            "position_size_pct": 0.0,
            "final_reasoning": f"Trader API down — no trade. {err_msg}",
        }
        return state

    # ── Step 7: Risk Manager (Groq) — degrade gracefully ─────────────────────

    print(f"  [7/8] Risk Management Team...")
    if portfolio is None:
        try:
            portfolio = get_portfolio()
        except Exception as e:
            print(f"  [!] Could not fetch portfolio: {e}")

    state = _safe_run(state, "risk_manager", risk_manager.run, {
        "risk_adjusted_decision": {
            "risk_assessment":   "approved",
            "approved":          True,
            "final_position_size": 0.05,
            "stop_loss_pct":     0.05,
            "reasoning":         "Risk manager unavailable — conservative defaults applied.",
        },
    })

    # ── Step 8: Fund Manager (Anthropic Opus) — CRITICAL ─────────────────────
    # Failure here means the final order cannot be placed.

    print(f"  [8/8] Fund Manager...")
    try:
        state = fund_manager.run(state, portfolio=portfolio, strategy_brief=strategy_brief)
    except Exception as e:
        err_msg = f"Fund Manager failed: {type(e).__name__}: {str(e)[:300]}"
        print(f"  [!] CRITICAL — {err_msg}")
        write_log(ticker, date, f"CRITICAL ERROR: {err_msg}")
        state.setdefault("errors", []).append({"agent": "fund_manager", "error": err_msg, "critical": True})
        state["final_order"] = {
            "action":          "hold",
            "qty":             0,
            "position_size_pct": 0.0,
            "final_reasoning": f"Fund Manager API down — no trade. {err_msg}",
        }
        return state

    # ── Summary ───────────────────────────────────────────────────────────────

    final_order = state.get("final_order", {})
    action      = final_order.get("action", "hold")
    qty         = final_order.get("qty", 0)
    reasoning   = final_order.get("final_reasoning", "")

    print(f"\n  FINAL DECISION: {action.upper()} {qty} shares of {ticker}")
    print(f"  Reasoning: {reasoning}")

    if state.get("errors"):
        print(f"  [!] Errors encountered: {len(state['errors'])} — check .tmp/logs/{date}/{ticker}.log")

    if not dry_run and action != "hold" and qty > 0:
        try:
            order_result = submit_order(ticker, action, qty)
            final_order["order_result"] = order_result
            print(f"  Order submitted: {order_result}")
        except Exception as e:
            print(f"  [!] Order submission failed: {e}")
            final_order["order_result"] = {"error": str(e)}
    elif dry_run:
        print(f"  [DRY RUN] Order not submitted.")

    write_log(ticker, date, f"Pipeline complete. Final order: {final_order}")
    return state


if __name__ == "__main__":
    from datetime import datetime
    today = datetime.today().strftime("%Y-%m-%d")
    result = run_pipeline("AAPL", today, dry_run=True)
    print(f"\nFull state saved to .tmp/state/{today}/AAPL.json")
