"""
Orchestrator: runs the full multi-agent pipeline for one ticker on one date.
Sequence: Fundamental → Sentiment → Technical → Researcher (Bull/Bear) →
          Trader → Risk Manager → Fund Manager → Submit Order
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.state_manager import init_state, write_log
from tools.paper_broker import get_portfolio, submit_order
from agents import fundamental_analyst, sentiment_analyst, technical_analyst
from agents import researcher, trader, risk_manager, fund_manager


def run_pipeline(ticker: str, date: str, dry_run: bool = True, portfolio: dict = None) -> dict:
    """
    Run the full trading agent pipeline for one ticker.

    dry_run=True: compute decision but do NOT submit the order (safe default)
    dry_run=False: submit the order via Alpaca paper trading API
    portfolio: real session portfolio dict (cash, equity, positions) — passed to
               fund_manager so it sizes orders against actual capital.

    Returns the final state dict.
    """
    print(f"\n{'='*60}")
    print(f"  TradingAgents Pipeline: {ticker} | {date}")
    print(f"{'='*60}")

    state = init_state(ticker, date)
    write_log(ticker, date, f"Pipeline started. dry_run={dry_run}")

    # Step 1: Fundamental Analysis
    print(f"  [1/7] Fundamental Analyst...")
    state = fundamental_analyst.run(state)

    # Step 2: Sentiment Analysis
    print(f"  [2/7] Sentiment Analyst...")
    state = sentiment_analyst.run(state)

    # Step 3: Technical Analysis
    print(f"  [3/7] Technical Analyst...")
    state = technical_analyst.run(state)

    # Step 4: Bull/Bear Researcher Debate
    print(f"  [4/7] Researcher Debate (Bull vs Bear)...")
    state = researcher.run(state)

    # Step 5: Trader Decision
    print(f"  [5/7] Trader...")
    state = trader.run(state)

    # Step 6: Risk Management
    print(f"  [6/7] Risk Management Team...")
    if portfolio is None:
        # Fall back to old paper_broker if no session portfolio supplied
        try:
            portfolio = get_portfolio()
        except Exception as e:
            print(f"  [!] Could not fetch portfolio: {e}")
    state = risk_manager.run(state)

    # Step 7: Fund Manager (final order) — receives real capital figures
    print(f"  [7/7] Fund Manager...")
    state = fund_manager.run(state, portfolio=portfolio)

    final_order = state.get("final_order", {})
    action = final_order.get("action", "hold")
    qty = final_order.get("qty", 0)
    reasoning = final_order.get("final_reasoning", "")

    print(f"\n  FINAL DECISION: {action.upper()} {qty} shares of {ticker}")
    print(f"  Reasoning: {reasoning}")

    if state.get("errors"):
        print(f"  [!] Errors encountered: {len(state['errors'])} — check .tmp/logs/{date}/{ticker}.log")

    # Submit order
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
