# Workflow: Add New Ticker to Watchlist

## Objective
Add a new stock ticker to the system's watchlist and validate it works
end-to-end before including it in live paper trading.

## Steps

### 1. Check data availability
Verify the ticker has data on all three sources:
```bash
python -c "
from tools.alpaca_data import get_ohlcv
from tools.finnhub_data import get_news, get_financials
from tools.reddit_sentiment import get_sentiment_summary

ticker = 'TSLA'  # replace with your ticker
print('Price data:', len(get_ohlcv(ticker, '2024-01-01', '2024-01-10')), 'bars')
print('News:', len(get_news(ticker, days_back=7)), 'articles')
print('Financials:', get_financials(ticker))
print('Reddit:', get_sentiment_summary(ticker, limit=5))
"
```

### 2. Run a dry-run single-day test
```bash
python main.py --ticker TSLA --dry-run
```
Review the log in `.tmp/logs/TODAY/TSLA.log`. Check all agents completed without errors.

### 3. Add to watchlist
Edit `config.py`:
```python
WATCHLIST = ["AAPL", "GOOGL", "NVDA", "MSFT", "AMZN", "TSLA"]  # add here
```

### 4. Run a short backtest to calibrate
```bash
python backtest.py --ticker TSLA --start 2024-01-01 --end 2024-01-31
```

## Notes
- Finnhub free tier may have limited news for small-cap or international stocks.
- Stocks without a strong Reddit presence will have neutral sentiment by default —
  this is expected behavior, not a bug.
- ETFs (SPY, QQQ) work for price/technical data but have no earnings/insider data.
