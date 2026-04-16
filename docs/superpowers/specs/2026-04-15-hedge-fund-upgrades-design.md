# Design: Hedge Fund Upgrades — 7 Risk & Realism Improvements

**Date:** 2026-04-15  
**Status:** Approved by user  
**Scope:** Improve paper trading realism and risk management without changing the agent pipeline

---

## Changes Overview

| # | Change | Files |
|---|--------|-------|
| 1 | Slippage on paper fills | `config.py`, `tools/paper_broker.py` |
| 2 | Portfolio beta cap | `config.py`, `morning_session.py` |
| 3 | Re-fetch price at execution (not analysis time) | `morning_session.py` |
| 4 | VIX rate-of-change filter | `config.py`, `tools/market_regime.py`, `morning_session.py` |
| 5 | Agent performance attribution in journal | `morning_session.py`, `eod_session.py` |
| 6 | Post-earnings blackout (1 day after) | `tools/market_regime.py`, `morning_session.py` |
| 7 | HYG credit spread risk-off signal | `tools/market_regime.py`, `morning_session.py` |

---

## 1. Slippage on Paper Fills

**Why:** Paper fills currently use mid-price. Real execution always incurs spread cost. Without this, paper results systematically overstate live performance.

**Implementation:**
- Add `SLIPPAGE_PCT = 0.0015` (15 bps) to `config.py`
- In `paper_broker.py` → `submit_order()`: apply slippage to `exec_price` before recording the fill
  - Buy: `exec_price *= (1 + SLIPPAGE_PCT)`
  - Sell: `exec_price *= (1 - SLIPPAGE_PCT)`
- Log the slippage cost in the order record

---

## 2. Portfolio Beta Cap

**Why:** Sector check prevents two names from the same GICS sector, but it's possible to hold NVDA + AMZN + META simultaneously — all high-beta tech-adjacent names with portfolio beta > 2.0. This is a concentrated directional bet, not a diversified book.

**Implementation:**
- Add approximate betas to `config.py` as `TICKER_BETA` dict (static values, updated manually)
- Add `MAX_PORTFOLIO_BETA = 1.5` to `config.py`
- Add `_portfolio_beta(portfolio, candidate_ticker)` in `morning_session.py`
  - Weights each position's beta by its portfolio fraction
  - Returns weighted average beta including the proposed new position
- If `_portfolio_beta()` > `MAX_PORTFOLIO_BETA`, block the trade and send a Telegram message explaining why
- Crypto is excluded from beta calculation (different risk profile)

**Beta values (approximate, based on 2-year trailing):**
```
AAPL: 1.2, NVDA: 1.8, MSFT: 1.1, GOOGL: 1.2, META: 1.3,
AMZN: 1.3, LLY: 0.5, JPM: 1.1, XOM: 0.8, CAT: 1.2,
WMT: 0.5, FCX: 1.6, NEE: 0.4, PLD: 1.0
```

---

## 3. Re-fetch Price at Execution

**Why:** The pipeline runs at 7:30 AM ET. The price captured in `_size_position()` is pre-market (previous close or thin pre-market quote). If the user approves at 9:45 AM, the actual fill should reflect the real open price — not a 2+ hour old quote.

**Implementation:**
- After `response == "approved"` in `morning_session.py`:
  - Re-fetch `exec_price = get_latest_price(ticker)`
  - Recalculate `stop_loss = round(exec_price * (1 - summary["_sl_pct_raw"]), 2)`
  - Recalculate `take_profit = round(exec_price * (1 + summary["_tp_pct_raw"]), 2)`
  - Recalculate `partial_profit_price = round(exec_price * (1 + summary["_sl_pct_raw"]), 2)`
  - Pass these to `open_position()` instead of the analysis-time values
  - Log `analysis_price` vs `exec_price` delta in the journal entry

---

## 4. VIX Rate-of-Change Filter

**Why:** A VIX of 22 held for 3 weeks indicates stable (if elevated) uncertainty. A VIX jumping 15→22 in 5 days indicates volatility expansion — the worst time to initiate longs. The absolute level misses this.

**Implementation:**
- Add `get_vix_roc(days=5)` to `market_regime.py`
  - Fetch 5 days of VIX OHLCV via `get_ohlcv("^VIX", ...)`
  - Return `(pct_change, label)` where `pct_change = (close_today - close_5d_ago) / close_5d_ago * 100`
  - Return `(None, "VIX RoC unavailable")` on error — never block on data failure
- Add `VIX_ROC_THRESHOLD = 20.0` to `config.py` (20% spike in 5 days)
- In `morning_session.py`, after the VIX level check:
  - Call `get_vix_roc()`
  - If RoC > threshold: apply `vix_multiplier *= 0.5` (additional halving)
  - Include VIX RoC in the analysis-starting Telegram message

---

## 5. Agent Performance Attribution

**Why:** With 7 agents running, there's no visibility into which signals are generating alpha. After 22 days, the system should be able to answer: "was the Technical Analyst right more often than the Fundamental Analyst?"

**Implementation:**
- In `morning_session.py`, extract agent signals from pipeline state and add to journal entry:
  ```python
  "agent_signals": {
      "fundamental": state.get("fundamental_analysis", {}).get("recommendation", ""),
      "technical":   state.get("technical_analysis", {}).get("signal", ""),
      "sentiment":   state.get("sentiment_analysis", {}).get("sentiment", ""),
      "bull_conviction": state.get("bull_case", "")[:50],
      "bear_conviction": state.get("bear_case", "")[:50],
      "trader_conviction": state.get("trader_decision", {}).get("conviction", ""),
      "risk_approved": state.get("risk_assessment", {}).get("approved", None),
  }
  ```
- In `eod_session.py`, when building the closed trade message, look up the journal entry for the ticker and include which agents flagged the trade
- Add a running attribution tally to the EOD message after session day 5 (enough data to be meaningful): shows win rate broken down by which agents were aligned vs opposed

---

## 6. Post-Earnings Blackout (1 Day After)

**Why:** Post-earnings price action is unpredictable regardless of beat/miss — a company can beat EPS by 20% and gap down on guidance. Don't initiate new positions the trading day after a major print.

**Implementation:**
- Add `had_earnings_recently(ticker, days=1)` to `market_regime.py`
  - Uses yfinance earnings calendar (same as existing `has_earnings_soon`)
  - Returns `True` if ticker had earnings in the last `days` calendar days
- In `morning_session.py` earnings loop, also call `had_earnings_recently()` and add to `earnings_blocked` set
- Telegram message for blocked tickers mentions "post-earnings cooldown" as reason

---

## 7. HYG Credit Spread Risk-Off Signal

**Why:** VIX measures equity vol. Credit spreads measure systemic risk. They diverge importantly — you can have stable VIX with widening credit spreads (early 2022). HYG (iShares High Yield ETF) is a clean proxy: when it's below its 20d MA by >2%, risk appetite is deteriorating across credit markets.

**Implementation:**
- Add `get_hyg_signal()` to `market_regime.py`
  - Fetch last 30 days of HYG OHLCV via Finnhub
  - Calculate 20-day MA
  - Return `(risk_off: bool, hyg_vs_ma_pct: float, label: str)`
  - Return `(False, None, "HYG unavailable")` on error — never block on data failure
- In `morning_session.py`, after VIX/RoC checks:
  - Call `get_hyg_signal()`
  - If `risk_off` and current VIX regime is MODERATE: downgrade to HIGH sizing (`vix_multiplier = min(vix_multiplier, 0.5)`)
  - Include HYG status in analysis-starting Telegram message

---

## Error Handling Principle

All new data fetches (VIX RoC, HYG) must fail gracefully: return a `None` or neutral value on exception. Never let a data fetch failure block trading. Log the failure and proceed.

---

## What Is NOT Changing

- The 7-agent LLM pipeline (orchestrator, agents)
- Position limits, portfolio heat, circuit breaker
- Session management, equity curve, dashboard
- Telegram approval flow
- GitHub Actions schedules
