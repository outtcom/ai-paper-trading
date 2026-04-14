"""
Backtesting runner: replays historical data through the full agent pipeline.
Simulates daily trading decisions over a date range and tracks portfolio performance.

Usage:
  python backtest.py --ticker AAPL --start 2024-01-01 --end 2024-03-29
  python backtest.py --start 2024-01-01 --end 2024-03-29   # all watchlist tickers
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import WATCHLIST, INITIAL_CAPITAL, MAX_POSITION_SIZE
from tools.market_data import get_ohlcv
from orchestrator import run_pipeline


def get_trading_dates(start: str, end: str) -> list[str]:
    """Generate weekday dates between start and end (no holiday filtering)."""
    dates = []
    current = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    while current <= end_dt:
        if current.weekday() < 5:
            dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


def run_backtest(tickers: list[str], start: str, end: str) -> dict:
    """
    Run the full pipeline for each ticker on each trading day.
    Track simulated portfolio performance.
    """
    dates = get_trading_dates(start, end)
    print(f"\nBacktest: {tickers} | {start} to {end} | {len(dates)} trading days")

    # Simple portfolio simulation
    portfolio = {t: {"cash": INITIAL_CAPITAL / len(tickers), "shares": 0, "avg_cost": 0} for t in tickers}
    performance = {t: [] for t in tickers}

    # Fetch all price data upfront for simulation
    price_data = {}
    for ticker in tickers:
        bars = get_ohlcv(ticker, start, end)
        price_data[ticker] = {b["date"]: b["close"] for b in bars}
        print(f"  Loaded {len(bars)} bars for {ticker}")

    for date in dates:
        print(f"\n--- {date} ---")
        for ticker in tickers:
            price = price_data[ticker].get(date)
            if price is None:
                print(f"  {ticker}: No price data for {date}, skipping")
                continue

            # Run pipeline (dry_run=True for backtesting — we simulate ourselves)
            state = run_pipeline(ticker, date, dry_run=True)
            order = state.get("final_order", {})
            action = order.get("action", "hold")
            position_size = order.get("position_size_pct", MAX_POSITION_SIZE)
            qty = order.get("qty", 0)

            p = portfolio[ticker]

            # Simulate execution
            if action == "buy" and p["cash"] > 0:
                buy_value = p["cash"] * position_size
                shares_to_buy = int(buy_value / price)
                if shares_to_buy > 0:
                    cost = shares_to_buy * price
                    p["cash"] -= cost
                    p["avg_cost"] = ((p["avg_cost"] * p["shares"]) + cost) / (p["shares"] + shares_to_buy)
                    p["shares"] += shares_to_buy
                    print(f"  {ticker}: BUY {shares_to_buy} @ ${price:.2f}")

            elif action == "sell" and p["shares"] > 0:
                sell_value = p["shares"] * price
                p["cash"] += sell_value
                pnl = sell_value - (p["avg_cost"] * p["shares"])
                print(f"  {ticker}: SELL {p['shares']} @ ${price:.2f} | P&L: ${pnl:.2f}")
                p["shares"] = 0
                p["avg_cost"] = 0
            else:
                print(f"  {ticker}: HOLD @ ${price:.2f}")

            equity = p["cash"] + p["shares"] * price
            performance[ticker].append({"date": date, "equity": round(equity, 2), "price": price})

    # Print summary
    print(f"\n{'='*60}")
    print("BACKTEST RESULTS")
    print(f"{'='*60}")
    for ticker in tickers:
        p = portfolio[ticker]
        initial = INITIAL_CAPITAL / len(tickers)
        final_price = list(price_data[ticker].values())[-1] if price_data[ticker] else 0
        final_equity = p["cash"] + p["shares"] * final_price
        cum_return = (final_equity - initial) / initial * 100
        print(f"\n  {ticker}:")
        print(f"    Initial Capital: ${initial:,.2f}")
        print(f"    Final Equity:    ${final_equity:,.2f}")
        print(f"    Cumulative Return: {cum_return:+.2f}%")

        # Buy & hold comparison
        first_price = list(price_data[ticker].values())[0] if price_data[ticker] else None
        if first_price and final_price:
            bh_return = (final_price - first_price) / first_price * 100
            print(f"    Buy & Hold Return: {bh_return:+.2f}%")
            print(f"    Alpha vs B&H: {cum_return - bh_return:+.2f}%")

    # Save results
    results_path = os.path.join(".tmp", f"backtest_{start}_{end}.json")
    with open(results_path, "w") as f:
        json.dump({"tickers": tickers, "start": start, "end": end, "performance": performance}, f, indent=2)
    print(f"\nDetailed results saved to {results_path}")

    return performance


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradingAgents Backtester")
    parser.add_argument("--ticker", type=str, help="Single ticker (default: all watchlist)")
    parser.add_argument("--start", type=str, required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, required=True, help="End date YYYY-MM-DD")
    args = parser.parse_args()

    tickers = [args.ticker.upper()] if args.ticker else WATCHLIST
    run_backtest(tickers, args.start, args.end)
