# Workflow: Daily Trading Cycle

## Objective
Run the full multi-agent trading pipeline for all watchlist tickers and submit
paper trading orders via Alpaca before market open.

## When to Run
Every weekday at approximately 9:00 AM ET (before market open at 9:30 AM ET).

## Required Inputs
- `.env` file with all API keys populated
- Alpaca paper trading account active
- Internet connection (APIs must be reachable)

## Steps

### 1. Verify environment
```bash
cd trading-system
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print('Anthropic:', bool(os.getenv('ANTHROPIC_API_KEY'))); print('Alpaca:', bool(os.getenv('ALPACA_API_KEY'))); print('Finnhub:', bool(os.getenv('FINNHUB_API_KEY')))"
```
All should print `True`. If not, check `.env`.

### 2. Run dry-run first (always)
```bash
python main.py --dry-run
```
Review the decisions in the output. If they look reasonable, proceed.

### 3. Submit paper orders
```bash
python main.py
```
Orders are submitted via Alpaca paper trading API.

### 4. Verify orders submitted
Check Alpaca paper trading dashboard or:
```bash
python -c "from tools.alpaca_broker import get_portfolio; import json; print(json.dumps(get_portfolio(), indent=2))"
```

### 5. Review logs
Logs are in `.tmp/logs/YYYY-MM-DD/`. One `.log` file per ticker with the full agent reasoning chain.

## Expected Outputs
- Paper orders placed in Alpaca
- State files in `.tmp/state/YYYY-MM-DD/TICKER.json`
- Reasoning logs in `.tmp/logs/YYYY-MM-DD/TICKER.log`

## Edge Cases & Known Issues
- **Weekend/holiday:** `main.py` skips non-weekdays automatically. For market holidays, orders won't fill — Alpaca will reject them.
- **API rate limits:** Finnhub free tier is 60 calls/minute. If you see rate limit errors, add `time.sleep(1)` between ticker cycles in `main.py`.
- **Reddit API down:** `reddit_sentiment.py` returns empty data gracefully. Sentiment agent will note data unavailability.
- **Missing price data:** `alpaca_data.py` falls back to yfinance automatically.
