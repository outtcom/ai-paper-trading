"""
Main entry point: runs the trading pipeline for all tickers in the watchlist.
Designed to be run once per trading day (e.g., before market open at 9:00 AM ET).

Usage:
  python main.py                    # paper trade all tickers (live mode)
  python main.py --dry-run          # compute decisions but don't submit orders
  python main.py --ticker AAPL      # run for a single ticker only
"""
import argparse
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import WATCHLIST
from orchestrator import run_pipeline


def is_trading_day() -> bool:
    """Basic check — skip weekends. Does not check market holidays."""
    return datetime.today().weekday() < 5  # Mon–Fri


def main():
    parser = argparse.ArgumentParser(description="TradingAgents Daily Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Compute decisions without submitting orders")
    parser.add_argument("--ticker", type=str, help="Run for a single ticker only")
    args = parser.parse_args()

    today = datetime.today().strftime("%Y-%m-%d")
    tickers = [args.ticker.upper()] if args.ticker else WATCHLIST

    print(f"\nTradingAgents | Date: {today} | Mode: {'DRY RUN' if args.dry_run else 'PAPER TRADING'}")
    print(f"Tickers: {tickers}")

    if not is_trading_day():
        print("Today is not a trading day (weekend). Exiting.")
        sys.exit(0)

    results = {}
    for ticker in tickers:
        try:
            state = run_pipeline(ticker, today, dry_run=args.dry_run)
            results[ticker] = state.get("final_order", {})
        except Exception as e:
            print(f"\n[ERROR] Pipeline failed for {ticker}: {e}")
            results[ticker] = {"error": str(e)}

    print(f"\n{'='*60}")
    print("DAILY SUMMARY")
    print(f"{'='*60}")
    for ticker, order in results.items():
        action = order.get("action", "ERROR")
        qty = order.get("qty", 0)
        print(f"  {ticker}: {action.upper()} {qty} shares")
    print(f"\nLogs: .tmp/logs/{today}/")
    print(f"State: .tmp/state/{today}/")


if __name__ == "__main__":
    main()
