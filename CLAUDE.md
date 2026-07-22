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

## Model Usage Rules (Multi-LLM via LiteLLM)

| Agent | Model | Provider | Why |
|-------|-------|----------|-----|
| Fundamental, Sentiment, Technical Analysts | `groq/llama-3.3-70b-versatile` | Groq | Structured formatting, 5× cheaper than Haiku |
| Bull/Bear Researchers | `openai/gpt-4o-mini` | OpenAI | Logical argumentation at low cost |
| Strategy Consultant | `claude-sonnet-4-6` | Anthropic | Daily macro brief needs high-quality synthesis |
| Trader | `claude-sonnet-4-6` | Anthropic | Best synthesis/cost balance |
| Risk Manager | `groq/llama-3.3-70b-versatile` | Groq | Structured JSON output, no need for premium model |
| Fund Manager | `claude-opus-4-6` | Anthropic | Non-negotiable — highest-stakes final gate |

**Cost profile: ~$0.60/day (down from ~$5.00/day, 88% reduction)**
- Groq API: `GROQ_API_KEY` in `.env` and GitHub secrets
- OpenAI API: `OPENAI_API_KEY` in `.env` and GitHub secrets
- All agents use `litellm.completion()` — unified interface, same semantics

**Never downgrade the Fund Manager below Opus.**

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
| Morning Session | Mon–Fri 9:45 AM (cron 7:30 AM EDT + ~2h15min queue) | `morning_session.py` |
| ICT FVG Scalping Scan | Mon–Fri ~10:30 AM | `scalping_scan.py` |
| Insider Activity + Political Signal Scan | Mon–Fri ~11:15 AM | `insider_scan.py` |
| Midday Position Monitor | Mon–Fri 12:00 PM | `midday_check.py` |
| Pre-Close Alert | Mon–Fri 3:30 PM | `preclose_alert.py` |
| End-of-Day Session | Mon–Fri 4:15 PM | `eod_session.py` |
| Weekly Intelligence Briefing | Sunday 6:00 PM | `weekly_briefing.py` |
| QA Analyst | After any workflow failure | `qa_analyst.py` |
| New Session Reset | Manual only (after 22:00 UTC weekdays / anytime weekends) | `tools/new_session.py` |

- Secrets (`ANTHROPIC_API_KEY`, `FINNHUB_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) are stored in GitHub repo secrets — never in code.
- Each workflow commits updated `docs/portfolio.json` back to the repo after running.
- Monitor runs: https://github.com/outtcom/ai-paper-trading/actions
- All workflows support `workflow_dispatch` for manual triggering from the Actions tab.

## Session Configuration

- **Session length**: 90 trading days (≈ 18 calendar weeks). Configured in `config.py` → `SESSION_DAYS` (single source of truth — `session_manager.py` imports it).
- **Benchmark**: SPY. Alpha calculated daily at EOD and tracked in weekly briefing.
- **Session 1**: 2026-04-14 → 2026-05-15 (22 days). Return: +0.36%, SPY: +5.04%, alpha: -4.68%. Root cause: only 16% capital deployed (target 68%).
- **Session 2 start**: 2026-05-15. Target: beat SPY over 30 days.
- **To start a new session**: trigger `new-session.yml` via GitHub Actions → Run workflow. Only after 22:00 UTC on a weekday (or any time on a weekend) to avoid cron collisions.

## Portfolio State (`docs/portfolio.json`)

Key fields Claude should know about:

| Field | Description |
|---|---|
| `positions[ticker].last_price` | Updated by midday + EOD after each price fetch — powers dashboard unrealized P&L |
| `open_orders[]` | Orders proposed by the morning pipeline; status: `pending → executed / rejected / expired` |
| `session.total_days` | 22 (one month). Change here to adjust session length. |

## Dashboard (`docs/index.html`)

Served via GitHub Pages. Refreshes every 60 s from `portfolio.json`.

**Sections:**
- KPI cards: Equity, Cash, Win Rate, Sharpe, Sortino, Calmar, Max Drawdown
- Benchmark bar: Our return vs SPY, alpha badge
- Equity curve chart
- **Open Positions**: Ticker, Qty, Entry, Current Price, Unrealized P&L (live), TP, SL, Partial Level, Days Held, Status
- **Open Orders**: All orders from the session with status (Pending / Executed / Rejected / Expired)
- Trade History, Trade Journal

## Known Issues & Fixes Applied

| Issue | Fix |
|---|---|
| Yahoo Finance rate-limits cloud IPs silently | `market_data.py` now uses Finnhub as primary source; yfinance is fallback only |
| Multiple workflows push `portfolio.json` simultaneously → rejected | Commit first, then `git pull --rebase`, then push (in all 6 workflows) |
| Duplicate `_` vs `-` workflow files running scripts twice | Removed all `snake_case` duplicates — only `kebab-case` filenames remain |
| `weekly_briefing.py` VIX None format error | Fixed: `{f'{vix:.1f}' if vix is not None else 'N/A'}` |
| `morning_session.py` fails silently on Anthropic credit exhaustion | Wraps `main()` in try/except — sends Telegram alert on crash |
| Reddit public JSON API blocked on GitHub Actions IPs | `get_sentiment_summary()` now falls back to Finnhub `news_sentiment()` endpoint when Reddit returns 0 posts — free tier, works from any IP. Sentiment analyst receives bullish/bearish % + buzz score instead of post list. |
| $5/day LLM cost unsustainable | Switched to multi-LLM via LiteLLM: Groq (analysts + risk), GPT-4o-mini (researchers), Sonnet (trader), Opus (fund manager only). Cost: ~$0.60/day |
| All pipeline tickers ran through LLM even if ineligible | Pre-filter in `_analyze_all()` skips earnings-blocked, same-sector, and volume-fail tickers before any API calls |
| `datetime.today()` returns UTC on GitHub Actions | All 6 session scripts use `datetime.now(ZoneInfo("America/New_York"))` for ET timestamps |
| EOD session-complete message dropped Sharpe/Win Rate lines | Fixed Python operator precedence bug — `spy_line` extracted as variable before f-string concatenation |
| `morning_session.py` NameError on sector fetch failure | `top3`/`bottom3` initialised to `""` before `try` block |
| Sector count said "across 3 sectors" | Fixed to use `len(SECTOR_MAP)` = 11 GICS sectors |
| SL/TP stored at 4 decimal places, displayed at 2 | `_size_position` now uses `round(..., 2)` matching `open_position` |
| `preclose_alert.py` showed double-negative `--X.Y%` | Conditional label + `abs()` when price crosses SL/TP |
| Pre-market gap signals blocked by volume ratio 1.0x < 1.5x | Finnhub `v=0` before 9:30 AM (intraday accumulator not started). Setting `current_vol=avg_vol` gives ratio=1.0 which still fails. Fix: use `premarket_vol` flag and **skip the ratio check entirely** before open. |
| `_yahoo_intraday_ohlcv()` fails locally on Windows | `ZoneInfo("America/New_York")` needs `tzdata` package on Windows. Fix: use plain UTC offset (`datetime.utcfromtimestamp(ts - 4*3600)`) — no tzdata needed, works on GitHub Actions Linux too. |
| Workflows arrive late due to GHA queue | Free-tier runner congestion during US market hours. Normal delays: 1–3h. On extreme congestion days (e.g. 2026-06-01) delays reached 4–7h and ALL workflows were skipped by their narrow DST gate. Fix applied 2026-06-01: widened all DST gates to 8h tolerance — pre-market 5AM–12PM, morning 7AM–3PM, scalping 8AM–3PM, midday 10AM–6PM, pre-close 1PM–8PM, EOD 3PM–10PM. Cron fire times unchanged. |
| Local `morning_session.py` fails with Anthropic credit error, GitHub Actions succeeds | Two separate API keys: local `.env` key vs GitHub repo secret. They have independent billing. Exhaust one → top up at console.anthropic.com. GitHub Actions pipeline unaffected. |
| Local scripts crash on Windows with `UnicodeEncodeError` on emoji print statements | cp1252 codec can't encode emoji. Fix: run with `PYTHONIOENCODING=utf-8 python script.py` locally. |
| Groq `llama-3.1-70b-versatile` decommissioned — all 5 Groq agents silently failing since Day 11 | Changed `MODELS["fast"]` in `config.py` to `groq/llama-3.3-70b-versatile` (direct successor, drop-in replacement). This affected: Fundamental Analyst, Sentiment Analyst, Technical Analyst, Risk Manager, Strategy Consultant. |
| ORB scalping: zero signals generated across 15 days | `bars[-1]` pointed to the ~11:00 AM bar (GHA runs 60-90 min late). Breakout had already reverted by then. Fixed: now uses `bars[n_range_bars]` — the actual first bar after the opening range (10:00 AM bar) — so signals fire at the correct ORB moment regardless of when GHA executes. |
| ORB scalping produced 0 signals even after the bars[] fix — choppy opens never produce clean breakouts | Replaced ORB entirely with **ICT FVG + OTE strategy** (2026-05-15). Instead of chasing breakouts above the range, scanner now finds institutional Fair Value Gaps during the displacement, enters on pullback to FVG or OTE zone (61.8%–78.6% Fib), targets displacement high/low liquidity, enforces 2:1 min RRR. `signal_type` changed from `scalping_orb` → `scalping_fvg`. `session_manager.py` filters updated to `startswith("scalping_")` for forward compatibility. |
| Session 1 alpha -4.68% — only 16% capital deployed at Day 15 (target 68%) | Root causes: (1) approval gate timed out/skipped trades during Fahad's off-hours, (2) MAX_CONCURRENT_POSITIONS=3 too restrictive, (3) Fund Manager HOLD bias. Fixes: approval gate removed (trades auto-execute after Telegram notify), MAX_CONCURRENT_POSITIONS raised 3→5, MAX_PORTFOLIO_HEAT raised 0.85→0.90, Fund Manager given 4 explicit HOLD-override conditions, strategy consultant primed with SPY alpha urgency logic. |
| `APPROVAL_TIMEOUT_SECONDS` referenced in morning_session.py after removal from config.py | Import removed from config import line. `send_approval_request` and `poll_for_response` replaced by `send_trade_notification` (broadcasts to both private+group, no buttons). morning-session.yml timeout reduced 90→30 min. |
| `eod_session.py` crash: `ValueError: could not convert string to float: '?'` (2 consecutive days, 2026-05-14 and 2026-05-15) | Root cause: when a position's trailing stop is updated in Step 2 AND the same position hits TP/SL and is closed in Step 3, the position no longer exists in the reloaded portfolio. `pos.get("stop_loss", "?")` returned `"?"`, and `float("?")` crashed. QA auto-fix applied `float()` but kept the `"?"` default — same crash next day. **Real fix**: use `None` default and skip the SL value if position is gone. `eod_session.py` line 361: `new_sl = pos.get("stop_loss")` + conditional append. |
| Gap & Go strategy draining day-trade pool (Session 1/2) | 1-year backtest confirmed losing: 18.6% win rate, profit factor 0.43, -7.18% return vs SPY +23.94%. Strategy removed entirely (2026-05-18). Pre-market gap scan kept for informational alerts only — no signals generated. Removed: `_detect_gap_and_go_signals()`, `GAP_AND_GO_TARGET_PCT`, `GAP_AND_GO_STOP_PCT`, `gap_and_go` from `_DT_LABELS`. |
| Swing strategy severely under-deployed — only 16% capital at Day 15 (target 68%) | LLM agents return 3–8% position sizes even under HIGH deployment priority. Fix (2026-05-18): added deployment urgency floor in `_size_position()` — minimum 20% when cash_ratio > 60%, 15% when cash_ratio > 40%. Hard cap raised from 25% → 30% per position. Also allow up to 2 new trades/day when portfolio < 50% deployed (`_pick_top_n(n)` replacing `_pick_best()`). Portfolio reloaded between the two trades so cash is accurate. |
| ICT FVG could not be backtested — Yahoo only provides today's 5-min bars | Added `get_intraday_ohlcv_history(ticker, days_back=60)` to `market_data.py` using yfinance (free, 60 days of 5-min history). Added `backtest_fvg_intraday()` to `backtest_rules.py` reusing FVG detection helpers from `scalping_scan.py`. Run: `python backtest_rules.py --fvg-intraday` |
| Multiplier stacking defeats deployment floor (Session 2 root cause) | Three multipliers compounded: `pos_frac × vix_mult × regime_mult × sector_mult`. When VIX=0.5× and regime=0.8×, a 20% urgency floor became 8% actual. Fix (2026-05-19): compute `macro_mult = min(vix_mult × regime_mult, 1.0)`, then when urgency floor kicks in, reset `macro_mult = 1.0` (critical) or `max(macro_mult, 0.80)` (behind). Final sizing: `cash × pos_frac × macro_mult × sector_mult`. |
| Fund Manager HOLD bias — "default rule" phrasing allowed Opus discretion | 4 rejection conditions existed but were framed as a "default rule" — Opus could HOLD even when none applied. Fix (2026-05-19): replaced with explicit DECISION TREE in `fund_manager.py` system prompt. If no rejection condition applies AND market_posture is risk-on or deployment is HIGH → BUY is mandatory, not discretionary. `risk_budget_multiplier` now clearly documented as sizing guidance, not a BUY/HOLD signal. |
| Risk Facilitator defaulted to caution with no majority rule | 3 perspectives (risk_seeking/neutral/risk_conservative) fed into a facilitator with no weighting rule. Under uncertainty, it consistently chose "reduced". Fix (2026-05-19): added explicit MAJORITY RULE to `risk_manager.py` facilitator prompt — if 2+ perspectives say "approve", output "approved". `final_position_size` must be ≥ median of 3 perspectives, not minimum. |
| Same-sector filter blocked all candidates when severely under-deployed | `_is_same_sector_open()` fired even with 80%+ cash, locking out entire sectors (all 7 Session 2 trades were Tech, so second Tech trade was always blocked). Fix (2026-05-19): filter now only enforces when `cash_ratio < 0.60`. When critically under-deployed, same-sector is allowed; same-day dedup still prevents two new same-sector entries via `seen_sectors` in `_pick_top_n`. |
| Strategy Consultant pacing threshold too loose, alpha urgency was label-only | 15% pacing gap threshold meant 13% behind said "on track". Alpha urgency was a string label, not a multiplier constraint. Fix (2026-05-19): threshold lowered to 10%; computed `min_multiplier` (1.0/1.2/1.4 based on pacing, up to 1.3 for critical alpha) passed into context as hard floor the model MUST NOT go below. |
| Watchlist had only 1 ticker per sector — Energy/Healthcare never traded | SECTOR_MAP had 1 ticker for Energy (XOM), Healthcare (LLY), Financials (JPM), etc. With same-sector filter active, a single bad day for that ticker = zero candidates from that sector. Fix (2026-05-19): added CVX, UNH, GS, MU, TSLA, GE, COST — 2 tickers per major sector so the pipeline always has a fallback within each sector. |
| All daily workflows lacked `timeout-minutes` — hung jobs blocked runners for up to 6h | Added `timeout-minutes: 30` to all 8 workflows that were missing it: `eod-session`, `premarket-check`, `midday-check`, `preclose-alert`, `scalping-scan`, `weekly-briefing`, `insider-scan`, `qa-analyst`. `morning-session` already had 60 min (needs market-open wait). |
| Scalping scan and insider scan fired at identical cron times — competed for free-tier runners | Both used `30 11 * * 1-5` (EDT). Fixed (2026-06-03): insider scan offset to `45 11 * * 1-5` — fires 15 min later, arrives ~11:15 AM ET instead of 11:00 AM ET. |
| Insider Telegram notification had no bullish/bearish sentiment label | Buy section showed amount and who bought but no sentiment signal. Fixed (2026-06-03): buys now show `🟢 Strongly Bullish` / `🟢 Bullish`; sells show `🔴 Bearish signal` / `🟡 Mildly bearish (likely routine)` based on `signal_strength` field. |
| "17 tickers" hardcoded in morning-session.yml notification was stale | SECTOR_MAP now has 21 stocks (expanded in Session 4). Fixed (2026-06-03): updated to "21 stocks". |
| `max_new` capped at 1 trade/day even with 47% cash and 2 open slots | Condition `deployed_pct < 0.50` triggered at 52.8% deployment, silently halving trade frequency for weeks. Fixed (2026-06-24): threshold raised to 0.70 (matching 70% deployment target). Also now reads `strategy_brief["target_new_positions"]` so the strategy consultant's urgency signal actually changes trade count. `max_new = min(max(urgency_new, target_from_brief), MAX_CONCURRENT_POSITIONS - open_count)`. |
| TP hardcoded at 2:1 regardless of signal conviction | High-conviction setups were being forced out at the same 2:1 level as low-confidence trades. Fixed (2026-06-24): conviction-based TP in `_size_position()` — high → 3×SL, medium → 2×SL (unchanged), low → 1.5×SL. |
| `max(1, int(max_usd / price))` forced 1-share buy even when price >> budget | With SPY at $755 and max_usd = $200, `int(200/755) = 0 → max(1, 0) = 1`, silently buying $555 over budget. Fixed (2026-06-24): replaced with guard — `qty = 1` only if `price ≤ max_usd × 1.5`; otherwise `qty = 0` and the caller's existing size-zero check skips the trade. |
| 7-agent pipeline ran on 20+ tickers including RSI<40 downtrends and RSI>78 overbought names | Wasted Groq quota and pipeline time on tickers the technical analyst would reject anyway. Fixed (2026-06-24): `_quick_prescreen(ticker)` added — runs volume + RSI-14 check in a single `get_ohlcv` call before any LLM calls. Skips if RSI<40 or RSI>78. Replaces the standalone `_has_volume_confirmation` call in `_analyze_all()`. |
| strategy_brief `conviction_floor` computed but never enforced | Consultant could output `conviction_floor="medium"` but low-conviction BUYs still made it through `_pick_top_n`. Fixed (2026-06-24): `_pick_top_n` now accepts `strategy_brief` and drops any BUY whose conviction ranks below the floor. |
| Dead money threshold too tight — positions at +1.2% still recycled as "not working" | `DEAD_MONEY_THRESHOLD = 0.01` closed positions with any gain under +1%. A +1.5% gain after 5 days is still good progress for a 2:1 R setup. Fixed (2026-06-24): raised to `0.02` (+2%) so only truly stagnant/flat positions get recycled. |
| DST gate silently skips all workflows when ET hour is 08 or 09 | `$(( $H * 60 ))` treated `08`/`09` as invalid octal → `value too great for base` error → comparison failed → `skip=true`. All 8 workflow files had the same bug. Fix (2026-06-25): prefix with `10#` to force base-10 — `10#$(TZ=America/New_York date +%H)`. Affected any workflow arriving between 8:00–9:59 AM ET. Note: `TZ=America/New_York` automatically handles EDT/EST transitions — no manual offset needed. |
| `midday-check.yml` silently skipped in winter when GHA queue < 2 hours | Cron fires at 13:00 UTC = 8:00 AM EST (winter). Gate lower bound was 10:00 AM (600 min), so a fast runner arriving at 8:xx AM EST fell below threshold and was skipped. Fix (2026-06-25): widened gate to 8:00 AM–6:00 PM (480–1080 min). |
| Morning session CANCELLED when GHA queue < 2h (June 30, July 2–3 2026) | `_wait_for_market_open(max_wait_minutes=75)` tries to sleep up to 75 min but `timeout-minutes: 60` in the YAML fires first → GHA cancels the job. Root cause: 10# octal fix correctly unblocked 8 AM ET arrivals, but those arrivals need 70 min to reach market open — exceeding the 60-min timeout. Fix (2026-07-03): raised `timeout-minutes` to 120 in `morning-session.yml`. |
| `_extract_signal()` searched only first line of LLM analyst reports — all verdicts (BULLISH/BEARISH/NEUTRAL) appear at END of report (section 5/7/4), not the first line | Changed to `re.search(r'\bKW\b', text.upper())` scanning full text. Added `import re`. Priority order: BULLISH > BUY, BEARISH > SELL, NEUTRAL > HOLD. Fix (2026-07-16): `morning_session.py`. |
| Dynamic universe-scanner picks (TMO, BAC, GIS, CMG, CVS etc.) assigned empty-string sector — bypassed avoid-sector filter, same-sector block, and `_pick_top_n` dedup | `get_top_movers_by_sector()` already returns `{sector: [tickers]}`. Added `TICKER_SECTOR.update({t: sec for sec, tickers in universe_picks.items() for t in tickers})` after the flattening step at line 722. In-place update propagates to all functions in the same process. Fix (2026-07-16): `morning_session.py`. |
| Audit log `risk_assessment` key wrong — `_s.get("risk_assessment", {})` always returns `{}`, so `risk_approved` in audit log was always `None` | Fixed key to `_s.get("risk_adjusted_decision", {})` and converted result to bool `(== "approved")`. Fix (2026-07-18): `morning_session.py` audit log block. |
| `agent_signals["risk_approved"]` stored string `"approved"/"reduced"/"rejected"` — all non-empty strings are truthy, so IC tracker `"overridden"` bucket was permanently empty; `"rejected"` trades appeared as "Risk ✓" in EOD messages | Fixed: store `(_ra == "approved") if _ra is not None else None` as boolean. Fix (2026-07-18): `morning_session.py` agent_signals dict, `tools/session_manager.py`. |
| Dynamic universe-scanner tickers (e.g. ORCL, CMG with upcoming earnings) bypassed earnings block — `has_earnings_soon` only ran on static `WATCHLIST`, checked before universe scan replaced `daily_watchlist` | Added delta earnings check after `daily_watchlist` is rebuilt: iterates `dynamic_stocks`, calls `has_earnings_soon`/`had_earnings_recently` for any ticker not already in `earnings_blocked`. Fix (2026-07-18): `morning_session.py`. |
| EOD `_agent_line` fundamental badge checked `"buy" in signal` but `_extract_signal` returns `"bullish"` (not `"buy"`) — Fund ✓ almost never appeared in EOD messages | Fixed to `any(k in signal for k in ("buy", "bullish"))`. Fix (2026-07-18): `eod_session.py` `_agent_line()`. |
| `_parse_sentiment` in agent_tracker didn't handle `"buy"` or `"sell"` from `_extract_signal` — classified them as `"neutral"`, corrupting IC sentiment stats | Added `"buy"` to bullish keywords list, `"sell"` to bearish. Fix (2026-07-18): `tools/agent_tracker.py`. |
| `penny-scan.yml` DST gate lower bound 510 min (8:30 AM ET) was above typical job arrival time (6–8 AM ET = 360–480 min) — penny scanner silently skipped every normal day since creation | Lowered gate to `300–780 min` (5:00 AM–1:00 PM ET, 8h window). Also added missing commit/push step (output was silently discarded). Fix (2026-07-18): `.github/workflows/penny-scan.yml`. |
| `eod-session.yml`, `midcap-scan.yml`, `preclose-alert.yml`, `premarket-check.yml` had 7-hour DST gate windows — fast-queue days (2–3h) risk silent skips | Widened each to exactly 8 hours: EOD 840–1320 (2PM–10PM), midcap 480–960 (8AM–4PM), preclose 720–1200 (12PM–8PM), premarket 300–780 (5AM–1PM). Fix (2026-07-18): four YAML files. |
| `midcap-scan.yml` had git identity configured but no commit/push step — midcap_scan.py output silently discarded | Added standard `git add → commit → pull --rebase → push` block. Fix (2026-07-18): `.github/workflows/midcap-scan.yml`. |
| `weekly-briefing.yml` had no DST gate — Sunday briefing could fire at any hour on congestion days | Added `dst-gate` job with 540–1440 min window (9 AM–midnight ET). Fix (2026-07-18): `.github/workflows/weekly-briefing.yml`. |
| `session-summary.yml` missing `timeout-minutes` — manual job could hang for 6h (GHA default) | Added `timeout-minutes: 30`. Fix (2026-07-18): `.github/workflows/session-summary.yml`. |
| `tools/session_manager.py` stored `opened_date` in UTC — on extreme GHA delay days (queue >8h, past midnight UTC), date would be one day ahead of ET journal `date`, breaking IC signal lookup in agent_tracker | Fixed: `datetime.now(ZoneInfo("America/New_York"))` for `opened_date`. Added `from zoneinfo import ZoneInfo` import. Fix (2026-07-18): `tools/session_manager.py`. |
| Groq 100k TPD (tokens/day) limit exhausted mid-run — all 5 Groq agents failed on the last 4 tickers (GLD, BTC-USD, ETH-USD, SOL-USD). Confirmed from GHA run 29918007823 (2026-07-22): `GroqException: tokens per day Limit 100000, Used 99580`. Risk manager alone consumed 57% of quota (4 calls/ticker × 3–5 analysts = 7 Groq calls/ticker × 20+ tickers). Root cause was per-ticker call volume, not req/day or req/min limits. | Fix (2026-07-22): (1) Collapsed `risk_manager.py` from 4 Groq calls to 1 (single combined perspectives + synthesis call) — ~43% reduction in per-ticker Groq volume. (2) Added `tools/groq_quota.py` — tracks cumulative tokens via `response.usage.total_tokens` after each call; at 85k automatically routes remaining 'fast' calls to `gpt-4o-mini` via `get_effective_fast_model()`. (3) Updated `tools/health_check.py`: `check_groq_quota()` sends Telegram when failover activates; `check_pipeline_errors()` now correlates failures by ticker×agent and names "Groq TPD exhausted" explicitly when RateLimitError pattern detected. |

## API Credit Notes

- **Anthropic API**: Only Trader (Sonnet) and Fund Manager (Opus) still use Claude. ~$0.47/day vs $5.00 previously. Monitor at console.anthropic.com.
- **Groq API**: Free tier — 6,000 req/day, 30 req/min, **100,000 tokens/day (TPD)**. Model: `llama-3.3-70b-versatile` (updated from decommissioned `llama-3.1-70b-versatile` on 2026-05-02). The TPD cap is the binding constraint at session scale — not req/day. When it's reached mid-run, `tools/groq_quota.py` automatically fails over to `gpt-4o-mini` so remaining tickers degrade gracefully instead of failing.
- **OpenAI API**: GPT-4o-mini for researchers. ~$0.03/day at current volume.
- **Finnhub free tier**: 60 API calls/minute. Sufficient for current watchlist size.

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

## Claude Code Hooks

Configured in `C:\Users\Fahad\.claude\settings.json` (global, applies to all projects):

| Event | Action |
|---|---|
| `Stop` | Plays `chimes.wav` — alerts Fahad that input is needed or a task is complete |
| `Notification` | Plays `chimes.wav` — alerts on mid-task notifications (e.g. background agents finishing) |
| `SessionStart` | Loads superpowers plugin context |

Sound file: `C:\Windows\Media\chimes.wav` (async, non-blocking)

**Env settings (also in `settings.json`):**
- `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` — blocks 1M context window; prevents unexpected billing
- `autoCompactEnabled: true` — auto-compacts conversation when context fills, keeping model quality high

---

## Self-Improvement Loop

When something breaks or you find a better approach:
1. Fix the tool/agent
2. Verify the fix works
3. Update the relevant workflow with what you learned
4. Move on
