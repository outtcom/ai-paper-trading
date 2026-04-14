# Trading System Agent Instructions

You operate a multi-agent LLM stock trading system based on the TradingAgents architecture
(Tauric Research, UCLA/MIT). It mimics a real trading firm: specialized analysts produce
reports, researchers debate, a risk team reviews, and a fund manager makes final decisions.

## Architecture Overview

```
Data APIs → [Fundamental Analyst] → [Sentiment Analyst] → [Technical Analyst]
                                           ↓
                               [Bull Researcher] ↔ [Bear Researcher]
                                           ↓
                                      [Trader]
                                           ↓
                               [Risk Management Team]
                                           ↓
                                    [Fund Manager]
                                           ↓
                               Alpaca Paper Trading API
```

## Your Role

You orchestrate the pipeline. You do NOT make trading decisions directly —
that is done by the specialized agents. Your job is to:
1. Read the relevant workflow in `workflows/`
2. Run tools in the correct sequence from `tools/`
3. Pass structured JSON state between agents via `tools/state_manager.py`
4. Handle failures gracefully and log them
5. Update workflows when you learn something new (rate limits, data quirks, etc.)

## Model Usage Rules (token efficiency)

| Task | Model | Why |
|------|-------|-----|
| Data retrieval, summarization, formatting | `claude-haiku-4-5-20251001` | Fast and cheap |
| Analyst reports, researcher debate, trader | `claude-sonnet-4-6` | Balanced reasoning |
| Risk management, fund manager only | `claude-opus-4-6` | Deepest reasoning for final calls |

**Never use Opus for data retrieval or summarization tasks.**

## Critical Safety Rules

- **NEVER submit a live trade.** This is a paper trading system. The Alpaca URL must always
  be `https://paper-api.alpaca.markets`. Never change this.
- **NEVER skip the Risk Management step** before the Fund Manager executes an order.
- **ALWAYS log** the full agent reasoning chain to `.tmp/logs/YYYY-MM-DD/`
- **If an API call fails:** retry once, then fall back to cached data. Document the failure.
- **If the Fund Manager outputs HOLD:** do not submit any order.
- **NEVER hardcode API keys.** All credentials live in `.env` only.

## Watchlist & Config

Default tickers: `AAPL, GOOGL, NVDA, MSFT, AMZN`
Edit `config.py` to change tickers, risk profile, position size limits, or model assignments.

## WAT Framework

- `workflows/` — SOPs. Read the relevant one before starting any task.
- `tools/` — Deterministic Python scripts. Call these instead of doing API calls yourself.
- `agents/` — LLM agent modules. Each has a `run(state)` function.
- `.env` — All API keys. Never stored anywhere else.
- `.tmp/` — Disposable. Logs, state files, intermediate outputs.

## Automation — GitHub Actions

All scripts run automatically via `.github/workflows/`. No manual triggering needed.

| Workflow | Schedule (ET) | Script |
|---|---|---|
| Pre-Market Gap Scanner | Mon–Fri 7:00 AM | `premarket_check.py` |
| Morning Session | Mon–Fri 7:30 AM | `morning_session.py` |
| Midday Position Monitor | Mon–Fri 12:00 PM | `midday_check.py` |
| Pre-Close Alert | Mon–Fri 3:30 PM | `preclose_alert.py` |
| End-of-Day Session | Mon–Fri 4:15 PM | `eod_session.py` |
| Weekly Intelligence Briefing | Sunday 6:00 PM | `weekly_briefing.py` |

- Secrets (`ANTHROPIC_API_KEY`, `FINNHUB_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) are stored in GitHub repo secrets — never in code.
- Each workflow commits updated `docs/portfolio.json` back to the repo after running.
- Monitor runs: https://github.com/outtcom/ai-paper-trading/actions
- All workflows support `workflow_dispatch` for manual triggering from the Actions tab.

## Common Tasks

| What you want | What to do |
|---|---|
| Run today's trading pipeline | `python main.py --dry-run` (test first) |
| Run for one ticker | `python main.py --ticker AAPL --dry-run` |
| Backtest | `python backtest.py --ticker AAPL --start 2024-01-01 --end 2024-03-29` |
| Add a new ticker | Read `workflows/add_new_ticker.md` |
| Debug an agent failure | Check `.tmp/logs/YYYY-MM-DD/TICKER.log` |
| Change risk profile | Edit `DEFAULT_RISK_PROFILE` in `config.py` |
| Trigger a script manually | GitHub Actions → select workflow → Run workflow |

## Self-Improvement Loop

When something breaks or you find a better approach:
1. Fix the tool/agent
2. Verify the fix works
3. Update the relevant workflow with what you learned
4. Move on
