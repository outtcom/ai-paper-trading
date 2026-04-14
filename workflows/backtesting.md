# Workflow: Backtesting

## Objective
Replay historical trading data through the full agent pipeline to evaluate
strategy performance before running with real (paper) capital.

## When to Use
- Before deploying a new configuration (new watchlist, new risk profile)
- When validating that changes to an agent haven't degraded performance
- To establish a baseline against buy-and-hold

## Cost Warning
Each backtest day costs approximately 11 LLM API calls per ticker.
A 3-month backtest on 1 ticker = ~65 trading days × 11 calls ≈ 715 LLM calls.
Estimate token cost before running: ~$2-5 per ticker per quarter at current Sonnet/Opus pricing.

## Steps

### 1. Choose a limited backtest window first
Start with 2 weeks to validate the pipeline end-to-end before running months of data.
```bash
python backtest.py --ticker AAPL --start 2024-01-02 --end 2024-01-12
```

### 2. Run the full backtest period (paper reference: Jan–Mar 2024)
```bash
python backtest.py --ticker AAPL --start 2024-01-01 --end 2024-03-29
```

### 3. Review results
- Check terminal output for cumulative return and alpha vs. buy-and-hold
- Detailed results saved to `.tmp/backtest_START_END.json`
- Agent logs in `.tmp/logs/YYYY-MM-DD/TICKER.log` for each day

### 4. Compare to paper benchmarks
Paper (TradingAgents original, using OpenAI):
- AAPL: ~26.6% cumulative return (Jan–Mar 2024)
- GOOGL: ~24.4%
- AMZN: ~23.2%

Our system (Claude-based) may differ due to model differences.

## Known Constraints
- Backtesting uses historical Finnhub news and Reddit data which is
  not fully time-locked — some future news may leak in.
- For strict no-look-ahead testing, use only Alpaca data (stock prices)
  and disable news/Reddit for historical dates older than 30 days.
- The backtest simulation is simplified: it does not model slippage,
  partial fills, or bid-ask spreads.
