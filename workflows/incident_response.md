# Workflow: Incident Response

## Objective
Diagnose and recover from failures in the trading pipeline without losing state
or submitting bad orders.

## Safety First
If you are unsure whether a bad order was submitted, check Alpaca immediately:
```bash
python -c "from tools.alpaca_broker import get_portfolio; import json; print(json.dumps(get_portfolio(), indent=2))"
```
Cancel any pending orders via the Alpaca paper trading dashboard if needed.

## Common Failures

### Agent returned an error / None decision
**Symptom:** Log shows `Fund Manager error — defaulting to HOLD`
**Action:**
1. Check `.tmp/logs/YYYY-MM-DD/TICKER.log` for the error message
2. Check `.tmp/state/YYYY-MM-DD/TICKER.json` — which field is null?
3. Fix the upstream agent and re-run for that ticker:
   ```bash
   python main.py --ticker AAPL --dry-run
   ```

### Finnhub rate limit (429 error)
**Symptom:** `finnhub_data.py` raises HTTP 429
**Action:**
1. Add a delay between tickers in `main.py`:
   ```python
   import time
   time.sleep(2)  # add after each ticker in the loop
   ```
2. Update this workflow with the rate limit behavior observed.

### Alpaca API down / paper trading unavailable
**Symptom:** `alpaca_broker.py` raises connection error
**Action:**
1. Run in dry-run mode: `python main.py --dry-run`
2. Check [status.alpaca.markets](https://status.alpaca.markets)
3. Resume when API recovers — orders from that day are lost (paper, no real impact)

### Reddit API authentication failure
**Symptom:** `praw.exceptions.ResponseException: 401`
**Action:**
1. Verify `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` in `.env`
2. Reddit app credentials expire — regenerate at reddit.com/prefs/apps
3. Sentiment agent will default to neutral if Reddit is unavailable

### LLM API error (Anthropic 500/529)
**Symptom:** `anthropic.APIStatusError` in agent log
**Action:**
1. Check [status.anthropic.com](https://status.anthropic.com)
2. Wait and retry — most are transient
3. If persistent, the agent will log the error and return a HOLD decision

## After Incident
1. Fix the root cause in the tool or agent
2. Verify the fix with a dry-run
3. Update this workflow with the new failure mode and resolution
