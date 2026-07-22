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

### Groq TPD (tokens/day) exhaustion
**Symptom:** `GroqException: Rate limit reached for model llama-3.3-70b-versatile ... tokens per day Limit 100000, Used 99XXX` in agent logs. Multiple tickers at the end of the analysis order fail across all 5 Groq agents simultaneously.

**Why it happens:** Groq free tier caps at 100,000 tokens/day. The morning session can consume 90k–100k tokens across 20+ tickers at 4 Groq calls/ticker (now reduced to 1 call/ticker for risk_manager). Once the budget is exhausted mid-run, every remaining Groq call for that day fails.

**Automatic mitigation (already in place):**
- `tools/groq_quota.py` tracks cumulative tokens via `response.usage.total_tokens` after each call.
- At 85,000 tokens (85% of cap), `get_effective_fast_model()` automatically routes to `openai/gpt-4o-mini` instead of Groq. No manual intervention needed.
- `check_groq_quota(date)` sends a private Telegram alert when failover activates.

**If the automatic failover didn't catch it (old pipeline or counter file missing):**
1. Check `.tmp/state/groq_quota_YYYY-MM-DD.json` — what was the `total_tokens` value?
2. The affected tickers are the ones processed last in `WATCHLIST` order (crypto + Index ETFs).
3. Re-run just those tickers manually: `python main.py --ticker BTC-USD --dry-run`
4. The re-run will use gpt-4o-mini since the quota file already shows failover_active=true.

**Prevention going forward:**
- `risk_manager.py` now uses 1 call/ticker (down from 4) — 43% reduction in daily Groq volume.
- If the session watchlist grows beyond ~30 tickers, consider requesting a Groq paid tier or further reducing max_tokens per analyst.

## After Incident
1. Fix the root cause in the tool or agent
2. Verify the fix with a dry-run
3. Update this workflow with the new failure mode and resolution
