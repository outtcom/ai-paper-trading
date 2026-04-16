# Hedge Fund Upgrades Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 7 risk/realism improvements to the paper trading system — slippage, portfolio beta cap, execution-time price re-fetch, VIX rate-of-change filter, agent performance attribution, post-earnings blackout, and HYG credit spread signal.

**Architecture:** Changes are additive — no existing logic is removed or restructured. Five files are touched in dependency order: `config.py` → `tools/paper_broker.py` → `tools/market_regime.py` → `morning_session.py` → `eod_session.py`. Each task is independently verifiable before moving to the next.

**Tech Stack:** Python 3.11, Finnhub REST API, yfinance (fallback), litellm, Telegram Bot API. All new data fetches fail gracefully — never block trading on missing data.

---

## File Map

| File | Change |
|------|--------|
| `config.py` | Add `SLIPPAGE_PCT`, `TICKER_BETA`, `MAX_PORTFOLIO_BETA`, `VIX_ROC_THRESHOLD` |
| `tools/paper_broker.py` | Apply slippage to `submit_order()` fills |
| `tools/market_regime.py` | Add `get_vix_roc()`, `get_hyg_signal()`, `had_earnings_recently()` |
| `morning_session.py` | Add `_portfolio_beta()`, 5 new checks in `main()`, re-fetch exec price, agent signals in journal |
| `eod_session.py` | Show agent signals for closed trades in EOD message |

---

## Task 1: Config — New Constants

**Files:**
- Modify: `config.py`

- [ ] **Step 1: Add slippage, beta, and VIX RoC constants to `config.py`**

  Open `config.py`. After the `BEARISH_REGIME_MULTIPLIER` line (line 68), insert:

  ```python
  # ---------------------------------------------------------------------------
  # Slippage
  # ---------------------------------------------------------------------------
  SLIPPAGE_PCT = 0.0015        # 15 bps on every paper fill — buys higher, sells lower

  # ---------------------------------------------------------------------------
  # Portfolio Beta Cap
  # ---------------------------------------------------------------------------
  # Approximate 2-year trailing betas vs SPY (update manually each quarter)
  TICKER_BETA = {
      "AAPL": 1.2, "NVDA": 1.8, "MSFT": 1.1,
      "GOOGL": 1.2, "META": 1.3,
      "AMZN": 1.3, "LLY": 0.5,  "JPM": 1.1,
      "XOM":  0.8, "CAT": 1.2,  "WMT": 0.5,
      "FCX":  1.6, "NEE": 0.4,  "PLD": 1.0,
  }
  MAX_PORTFOLIO_BETA = 1.5     # weighted avg beta cap (crypto excluded)

  # ---------------------------------------------------------------------------
  # VIX Rate-of-Change
  # ---------------------------------------------------------------------------
  VIX_ROC_THRESHOLD = 20.0    # % rise in VIX over 5 days → additional 0.5× size cut
  ```

- [ ] **Step 2: Verify the file is importable**

  ```bash
  cd "c:/Users/Fahad/OneDrive/Desktop/Claude Projects/Car Cleaning Gel Amazon/trading-system"
  python -c "from config import SLIPPAGE_PCT, TICKER_BETA, MAX_PORTFOLIO_BETA, VIX_ROC_THRESHOLD; print('SLIPPAGE_PCT:', SLIPPAGE_PCT, '| MAX_PORTFOLIO_BETA:', MAX_PORTFOLIO_BETA)"
  ```

  Expected output:
  ```
  SLIPPAGE_PCT: 0.0015 | MAX_PORTFOLIO_BETA: 1.5
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add config.py
  git commit -m "feat: add slippage, beta cap, and VIX RoC constants to config"
  ```

---

## Task 2: Slippage on Paper Fills

**Files:**
- Modify: `tools/paper_broker.py`

- [ ] **Step 1: Apply slippage inside `submit_order()`**

  Open `tools/paper_broker.py`. Find this block near line 81:

  ```python
  p = _load_portfolio()
  exec_price = price or get_latest_price(ticker)
  ```

  Replace it with:

  ```python
  from config import SLIPPAGE_PCT
  p = _load_portfolio()
  exec_price = price or get_latest_price(ticker)
  # Realistic fill: buys execute slightly above mid, sells slightly below
  if action == "buy":
      exec_price = round(exec_price * (1 + SLIPPAGE_PCT), 4)
  elif action == "sell":
      exec_price = round(exec_price * (1 - SLIPPAGE_PCT), 4)
  ```

- [ ] **Step 2: Add `slippage_pct` to the order record**

  Find the `order = { ... }` dict (around line 112). Add one field:

  ```python
  order = {
      "order_id":    f"SIM-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
      "ticker":      ticker,
      "action":      action,
      "qty":         qty,
      "exec_price":  exec_price,
      "slippage_pct": SLIPPAGE_PCT,
      "total_value": round(exec_price * qty, 2),
      "status":      "filled",
      "timestamp":   datetime.utcnow().isoformat(),
  }
  ```

- [ ] **Step 3: Verify slippage is applied**

  ```bash
  cd "c:/Users/Fahad/OneDrive/Desktop/Claude Projects/Car Cleaning Gel Amazon/trading-system"
  python -c "
  from tools.paper_broker import submit_order
  from config import SLIPPAGE_PCT
  # Simulate a buy at a known price
  result = submit_order('AAPL', 'buy', 1, price=100.00)
  expected_min = 100.00 * (1 + SLIPPAGE_PCT)
  print('exec_price:', result['exec_price'])
  print('expected ~:', round(expected_min, 4))
  print('slippage applied:', result['exec_price'] > 100.00)
  print('slippage_pct field:', result.get('slippage_pct'))
  "
  ```

  Expected output:
  ```
  exec_price: 100.15
  expected ~: 100.15
  slippage applied: True
  slippage_pct field: 0.0015
  ```

  > Note: This writes to `.tmp/portfolio.json`. Run `python -c "from tools.paper_broker import reset_portfolio; reset_portfolio()"` afterward to clean up.

- [ ] **Step 4: Reset portfolio after test**

  ```bash
  python -c "from tools.paper_broker import reset_portfolio; reset_portfolio()"
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add tools/paper_broker.py
  git commit -m "feat: apply 15bps slippage to paper broker fills"
  ```

---

## Task 3: New Market Regime Functions

**Files:**
- Modify: `tools/market_regime.py`

Three functions are added at the bottom of the file (before the `if __name__ == "__main__":` block if one exists, otherwise at end of file). They are independent of each other.

- [ ] **Step 1: Add `get_vix_roc()` function**

  Append to `tools/market_regime.py`:

  ```python
  def get_vix_roc(days: int = 5) -> tuple:
      """
      Returns (pct_change, label) for VIX over the last `days` trading days.
      pct_change is positive when VIX is rising (e.g. 22.5 = +22.5% over 5d).
      Returns (None, "VIX RoC unavailable") on any failure — never blocks trading.
      """
      try:
          end   = datetime.today().strftime("%Y-%m-%d")
          start = (datetime.today() - timedelta(days=days * 2 + 7)).strftime("%Y-%m-%d")
          bars  = get_ohlcv("^VIX", start, end)
          if len(bars) < days + 1:
              return None, "VIX RoC unavailable (insufficient data)"
          old_close = bars[-(days + 1)]["close"]
          new_close = bars[-1]["close"]
          if old_close <= 0:
              return None, "VIX RoC unavailable (zero price)"
          pct   = round((new_close - old_close) / old_close * 100, 1)
          sign  = "+" if pct >= 0 else ""
          label = f"VIX RoC {sign}{pct:.1f}% ({days}d)"
          return pct, label
      except Exception:
          return None, "VIX RoC unavailable"
  ```

- [ ] **Step 2: Add `get_hyg_signal()` function**

  Append to `tools/market_regime.py`:

  ```python
  def get_hyg_signal() -> tuple:
      """
      Check HYG (iShares High Yield ETF) vs its 20-day MA as a credit spread proxy.
      Returns (risk_off: bool, pct_vs_ma: float | None, label: str).
      risk_off = True when HYG is >2% below its 20-day MA.
      Returns (False, None, "HYG unavailable") on any failure — never blocks trading.
      """
      try:
          end   = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")
          start = (datetime.today() - timedelta(days=40)).strftime("%Y-%m-%d")
          bars  = get_ohlcv("HYG", start, end)
          if len(bars) < 20:
              return False, None, "HYG unavailable (insufficient data)"
          closes      = [b["close"] for b in bars]
          current     = closes[-1]
          ma20        = round(sum(closes[-20:]) / 20, 4)
          pct_vs_ma   = round((current - ma20) / ma20 * 100, 2)
          risk_off    = pct_vs_ma < -2.0
          if risk_off:
              label = f"HYG RISK-OFF ({pct_vs_ma:+.1f}% vs 20d MA) ⚠️"
          else:
              label = f"HYG OK ({pct_vs_ma:+.1f}% vs 20d MA) ✅"
          return risk_off, pct_vs_ma, label
      except Exception:
          return False, None, "HYG unavailable"
  ```

- [ ] **Step 3: Add `had_earnings_recently()` function**

  Append to `tools/market_regime.py`:

  ```python
  def had_earnings_recently(ticker: str, days: int = 1) -> bool:
      """
      Return True if ticker reported earnings within the last `days` calendar days.
      Always returns False for crypto tickers.
      Uses the same yfinance approach as has_earnings_soon() but looks backward.
      """
      if "-USD" in ticker:
          return False
      try:
          import yfinance as yf
          t   = yf.Ticker(ticker)
          now = datetime.now()

          # Try earnings_dates first (newer yfinance)
          try:
              ed = t.earnings_dates
              if ed is not None and not ed.empty:
                  for idx in ed.index:
                      dt   = idx.to_pydatetime().replace(tzinfo=None)
                      diff = (now - dt).days   # positive = past
                      if 0 <= diff <= days:
                          return True
          except Exception:
              pass

          # Fallback: calendar dict
          try:
              cal = t.calendar
              if isinstance(cal, dict):
                  dates = cal.get("Earnings Date", [])
                  if not isinstance(dates, list):
                      dates = [dates]
                  for d in dates:
                      if hasattr(d, "to_pydatetime"):
                          d = d.to_pydatetime()
                      if hasattr(d, "replace"):
                          d = d.replace(tzinfo=None)
                          diff = (now - d).days
                          if 0 <= diff <= days:
                              return True
          except Exception:
              pass

      except Exception:
          pass
      return False
  ```

- [ ] **Step 4: Verify all three functions are importable and return expected types**

  ```bash
  cd "c:/Users/Fahad/OneDrive/Desktop/Claude Projects/Car Cleaning Gel Amazon/trading-system"
  python -c "
  from tools.market_regime import get_vix_roc, get_hyg_signal, had_earnings_recently

  roc, roc_label = get_vix_roc()
  print('VIX RoC:', roc, '|', roc_label)
  assert roc is None or isinstance(roc, float), 'get_vix_roc pct must be float or None'

  risk_off, hyg_pct, hyg_label = get_hyg_signal()
  print('HYG:', risk_off, '|', hyg_pct, '|', hyg_label)
  assert isinstance(risk_off, bool), 'get_hyg_signal risk_off must be bool'

  result = had_earnings_recently('BTC-USD')
  print('BTC-USD had_earnings_recently:', result)
  assert result == False, 'crypto must always return False'
  print('All assertions passed.')
  "
  ```

  Expected output (values will vary, types must match):
  ```
  VIX RoC: 5.2 | VIX RoC +5.2% (5d)     ← or "VIX RoC unavailable" if yfinance rate-limited
  HYG: False | -0.8 | HYG OK (-0.8% vs 20d MA) ✅
  BTC-USD had_earnings_recently: False
  All assertions passed.
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add tools/market_regime.py
  git commit -m "feat: add get_vix_roc, get_hyg_signal, had_earnings_recently to market_regime"
  ```

---

## Task 4: Morning Session — All Five Changes

**Files:**
- Modify: `morning_session.py`

This task has five sub-changes. Apply them in the order listed — each builds on the state left by the previous.

### 4a: Update imports

- [ ] **Step 1: Expand the `from config import` line**

  Find the existing import (lines 32–35):
  ```python
  from config import (
      WATCHLIST, APPROVAL_TIMEOUT_SECONDS,
      MAX_CONCURRENT_POSITIONS, MAX_PORTFOLIO_HEAT, MIN_VOLUME_RATIO,
      BEARISH_REGIME_MULTIPLIER, TICKER_SECTOR, SECTOR_MAP,
  )
  ```

  Replace with:
  ```python
  from config import (
      WATCHLIST, APPROVAL_TIMEOUT_SECONDS,
      MAX_CONCURRENT_POSITIONS, MAX_PORTFOLIO_HEAT, MIN_VOLUME_RATIO,
      BEARISH_REGIME_MULTIPLIER, TICKER_SECTOR, SECTOR_MAP,
      TICKER_BETA, MAX_PORTFOLIO_BETA, VIX_ROC_THRESHOLD,
  )
  ```

- [ ] **Step 2: Expand the `from tools.market_regime import` line**

  Find:
  ```python
  from tools.market_regime import get_vix_multiplier, get_market_trend, has_earnings_soon, is_event_blocked
  ```

  Replace with:
  ```python
  from tools.market_regime import (
      get_vix_multiplier, get_market_trend, has_earnings_soon, is_event_blocked,
      get_vix_roc, get_hyg_signal, had_earnings_recently,
  )
  ```

- [ ] **Step 3: Verify imports work**

  ```bash
  cd "c:/Users/Fahad/OneDrive/Desktop/Claude Projects/Car Cleaning Gel Amazon/trading-system"
  python -c "import morning_session; print('imports OK')"
  ```

  Expected: `imports OK`

### 4b: Add `_portfolio_beta()` helper

- [ ] **Step 4: Add `_portfolio_beta()` after the `_is_same_sector_open()` function**

  Find the line (around line 109):
  ```python
  # ---------------------------------------------------------------------------
  # Pipeline helpers
  # ---------------------------------------------------------------------------
  ```

  Insert the new function immediately before that comment block:

  ```python
  def _portfolio_beta(portfolio: dict, candidate_ticker: str, candidate_usd: float) -> float:
      """
      Return the weighted average portfolio beta if the candidate position is added.
      Crypto positions are excluded (different risk profile).
      Uses TICKER_BETA from config; defaults to 1.0 for unknown tickers.
      """
      total_equity = portfolio.get("equity", portfolio.get("initial_capital", 5000))
      if total_equity <= 0:
          return 1.0

      weighted_beta = 0.0
      total_weight  = 0.0

      for ticker, pos in portfolio.get("positions", {}).items():
          if "-USD" in ticker:
              continue
          beta = TICKER_BETA.get(ticker, 1.0)
          try:
              price     = get_latest_price(ticker)
              pos_value = pos["qty"] * price
          except Exception:
              pos_value = pos.get("cost_basis", 0)
          weight         = pos_value / total_equity
          weighted_beta += beta * weight
          total_weight  += weight

      if candidate_ticker and "-USD" not in candidate_ticker and candidate_usd > 0:
          beta           = TICKER_BETA.get(candidate_ticker, 1.0)
          weight         = candidate_usd / total_equity
          weighted_beta += beta * weight
          total_weight  += weight

      return round(weighted_beta, 2) if total_weight > 0 else 1.0


  # ---------------------------------------------------------------------------
  # Pipeline helpers
  # ---------------------------------------------------------------------------
  ```

### 4c: VIX Rate-of-Change + HYG checks in `main()`

- [ ] **Step 5: Add VIX RoC check immediately after the VIX extreme check**

  Find this block in `main()` (around line 310):
  ```python
  # ── Market regime overlay (SPY vs 200d MA) ────────────────────────────
  spy_trend    = get_market_trend("SPY")
  ```

  Insert between the VIX extreme check and the regime overlay:

  ```python
      # ── VIX rate-of-change filter ─────────────────────────────────────────
      vix_roc, vix_roc_label = get_vix_roc()
      if vix_roc is not None and vix_roc > VIX_ROC_THRESHOLD:
          print(f"[morning] VIX RoC {vix_roc:.1f}% > {VIX_ROC_THRESHOLD}% — additional 0.5× size cut")
          vix_multiplier = round(vix_multiplier * 0.5, 2)
          vix_label      = f"{vix_label} | RoC {vix_roc:+.1f}% 🔺"
      else:
          vix_roc_label = vix_roc_label  # already set; keep as-is

      # ── Market regime overlay (SPY vs 200d MA) ────────────────────────────
      spy_trend    = get_market_trend("SPY")
  ```

- [ ] **Step 6: Add HYG check immediately after the regime overlay block**

  Find (around line 325):
  ```python
      # ── Concurrent position limit ──────────────────────────────────────────
  ```

  Insert before it:

  ```python
      # ── HYG credit spread check ───────────────────────────────────────────
      hyg_risk_off, _hyg_pct, hyg_label = get_hyg_signal()
      print(f"[morning] HYG: {hyg_label}")
      if hyg_risk_off and vix_multiplier > 0.5:
          print(f"[morning] HYG risk-off — capping sizing at 0.5×")
          vix_multiplier = min(vix_multiplier, 0.5)
          vix_label      = f"{vix_label} | {hyg_label}"

      # ── Concurrent position limit ──────────────────────────────────────────
  ```

- [ ] **Step 7: Include VIX RoC and HYG in the analysis-starting Telegram message**

  Find the `send_message(...)` call around line 371 that reads:
  ```python
      send_message(
          f"🔍 <b>Day {session_day}/{total_days}</b> — Analysing {len(WATCHLIST)} tickers "
          f"across {len(SECTOR_MAP)} sectors...\n"
          f"VIX: {vix_label}  |  Regime: {regime_label}\n"
          f"Sector leaders: {top3 or 'N/A'}\n"
          f"<i>Back in ~15–20 min with the best trade.</i>"
      )
  ```

  Replace with:
  ```python
      send_message(
          f"🔍 <b>Day {session_day}/{total_days}</b> — Analysing {len(WATCHLIST)} tickers "
          f"across {len(SECTOR_MAP)} sectors...\n"
          f"VIX: {vix_label}  |  Regime: {regime_label}\n"
          f"Credit: {hyg_label}\n"
          f"Sector leaders: {top3 or 'N/A'}\n"
          f"<i>Back in ~15–20 min with the best trade.</i>"
      )
  ```

### 4d: Post-earnings blackout (both sides)

- [ ] **Step 8: Expand earnings loop to check 5 days forward and 1 day backward**

  Find the earnings loop (around line 350):
  ```python
      # ── Earnings check ────────────────────────────────────────────────────
      earnings_blocked = set()
      for t in WATCHLIST:
          e = has_earnings_soon(t, days=3)
          if e["has_earnings"]:
              earnings_blocked.add(t)
              print(f"[morning] {t} blocked — earnings {e['date']}")
  ```

  Replace with:
  ```python
      # ── Earnings check ────────────────────────────────────────────────────
      earnings_blocked = set()
      for t in WATCHLIST:
          e = has_earnings_soon(t, days=5)
          if e["has_earnings"]:
              earnings_blocked.add(t)
              print(f"[morning] {t} blocked — upcoming earnings {e['date']}")
          elif had_earnings_recently(t, days=1):
              earnings_blocked.add(t)
              print(f"[morning] {t} blocked — post-earnings cooldown (reported yesterday)")
  ```

### 4e: Portfolio beta cap check

- [ ] **Step 9: Add beta cap check after `_pick_best()` and before `_size_position()`**

  Find this block (around line 395):
  ```python
      # ── Size the position ──────────────────────────────────────────────────
      summary = _size_position(ticker, state, cash, vix_multiplier, regime_mult)
  ```

  Insert before it:

  ```python
      # ── Portfolio beta cap ────────────────────────────────────────────────
      if ticker and "-USD" not in ticker:
          estimated_usd = cash * 0.25 * vix_multiplier * regime_mult
          port_beta     = _portfolio_beta(portfolio, ticker, estimated_usd)
          print(f"[morning] Portfolio beta (incl {ticker}): {port_beta:.2f}  cap={MAX_PORTFOLIO_BETA}")
          if port_beta > MAX_PORTFOLIO_BETA:
              send_message(
                  f"📊 <b>Beta Cap — Day {session_day}/{total_days}</b>\n\n"
                  f"Adding {ticker} would push portfolio β to {port_beta:.2f} "
                  f"(max {MAX_PORTFOLIO_BETA}).\n"
                  f"Staying in cash to maintain diversification.\n\n"
                  f"Session equity: <b>${equity:,.2f}</b>"
              )
              record_equity(equity)
              return

      # ── Size the position ──────────────────────────────────────────────────
      summary = _size_position(ticker, state, cash, vix_multiplier, regime_mult)
  ```

### 4f: Re-fetch execution price after approval

- [ ] **Step 10: Replace the `response == "approved"` block's price usage**

  Find the block starting with `if response == "approved":` (around line 425):
  ```python
      if response == "approved":
          update_open_order(ticker, "executed")
          open_position(
              ticker         = ticker,
              qty            = summary["qty"],
              entry_price    = summary["current_price"],
              stop_loss_pct  = summary["_sl_pct_raw"],
              take_profit_pct= summary["_tp_pct_raw"],
              journal_note   = summary["_full_why"][:500],
          )
          add_journal_entry({
              "date":        today,
              "day":         session_day,
              "ticker":      ticker,
              "action":      "BUY",
              "entry_price": summary["current_price"],
  ```

  Replace the entire `if response == "approved":` block with:

  ```python
      if response == "approved":
          # Re-fetch price at execution time — not the pre-market analysis-time quote
          try:
              exec_price = get_latest_price(ticker)
          except Exception:
              exec_price = summary["current_price"]
          exec_stop_loss   = round(exec_price * (1 - summary["_sl_pct_raw"]), 2)
          exec_take_profit = round(exec_price * (1 + summary["_tp_pct_raw"]), 2)
          price_delta_pct  = round((exec_price - summary["current_price"]) / summary["current_price"] * 100, 2)
          print(f"[morning] Analysis: ${summary['current_price']:.2f} → Exec: ${exec_price:.2f} ({price_delta_pct:+.2f}%)")

          update_open_order(ticker, "executed")
          open_position(
              ticker          = ticker,
              qty             = summary["qty"],
              entry_price     = exec_price,
              stop_loss_pct   = summary["_sl_pct_raw"],
              take_profit_pct = summary["_tp_pct_raw"],
              journal_note    = summary["_full_why"][:500],
          )

          # Capture which agents were aligned on this trade
          agent_signals = {
              "fundamental":       state.get("fundamental_analysis", {}).get("recommendation", ""),
              "technical":         state.get("technical_analysis",   {}).get("signal", ""),
              "sentiment":         state.get("sentiment_analysis",   {}).get("sentiment", ""),
              "trader_conviction": state.get("trader_decision",      {}).get("conviction", ""),
              "risk_approved":     state.get("risk_assessment",      {}).get("approved", None),
          }

          add_journal_entry({
              "date":           today,
              "day":            session_day,
              "ticker":         ticker,
              "action":         "BUY",
              "analysis_price": summary["current_price"],
              "entry_price":    exec_price,
              "price_delta_pct": price_delta_pct,
              "qty":            summary["qty"],
              "conviction":     summary["conviction"],
              "score":          score,
              "sector":         summary["sector"],
              "sector_rank":    summary["sector_rank"],
              "stop_loss":      exec_stop_loss,
              "take_profit":    exec_take_profit,
              "vix_label":      vix_label,
              "regime":         regime_label,
              "rationale":      summary["_full_why"],
              "bull_case":      summary["bull_case"],
              "bear_case":      summary["bear_case"],
              "agent_signals":  agent_signals,
          })
          send_message(
              f"✅ <b>Trade Executed — {ticker}</b>\n\n"
              f"BUY {summary['qty']} @ ${exec_price:.2f}\n"
              f"Deployed: ${round(exec_price * summary['qty'], 2):.2f}  |  "
              f"Sector: {summary['sector']} (rank #{summary['sector_rank']})\n\n"
              f"TP: ${exec_take_profit:.2f}  |  SL: ${exec_stop_loss:.2f}\n"
              f"Partial profit triggers at: "
              f"${round(exec_price * (1 + summary['_sl_pct_raw']), 2):.2f} (1:1 R/R)\n\n"
              f"<i>EOD check at 4:15 PM ET.</i>"
          )
          record_equity(equity)
  ```

- [ ] **Step 11: Verify `morning_session.py` is importable and `main()` is callable (dry run check)**

  ```bash
  cd "c:/Users/Fahad/OneDrive/Desktop/Claude Projects/Car Cleaning Gel Amazon/trading-system"
  python -c "
  import morning_session
  import inspect
  # Check _portfolio_beta exists and has right signature
  sig = inspect.signature(morning_session._portfolio_beta)
  params = list(sig.parameters.keys())
  assert params == ['portfolio', 'candidate_ticker', 'candidate_usd'], f'wrong params: {params}'
  print('_portfolio_beta signature OK:', params)
  print('morning_session imports OK')
  "
  ```

  Expected:
  ```
  _portfolio_beta signature OK: ['portfolio', 'candidate_ticker', 'candidate_usd']
  morning_session imports OK
  ```

- [ ] **Step 12: Commit**

  ```bash
  git add morning_session.py
  git commit -m "feat: add beta cap, VIX RoC, HYG, post-earnings, exec price re-fetch to morning session"
  ```

---

## Task 5: EOD Agent Attribution

**Files:**
- Modify: `eod_session.py`

- [ ] **Step 1: Add agent signal lookup inside `_build_eod_message()` for closed trades**

  Open `eod_session.py`. Find the closed trade loop inside `_build_eod_message()` (around line 209):

  ```python
      # TP/SL closures today
      for trade in closed_trades:
          if trade["reason"] == "take_profit":
              emoji, label = "🎯", "TAKE PROFIT"
          else:
              emoji, label = "🛑", "STOP LOSS"
          sign = "+" if trade["pnl"] >= 0 else ""
          lines.append(
              f"{emoji} <b>{label} — {trade['ticker']}</b>\n"
              f"Entry: ${trade['entry_price']:.2f} → Exit: ${trade['exit_price']:.2f}\n"
              f"P&amp;L: {sign}${trade['pnl']:.2f} ({sign}{trade['pnl_pct']:.1f}%)\n"
          )
  ```

  Replace with:

  ```python
      # TP/SL closures today
      journal = portfolio.get("journal", [])
      for trade in closed_trades:
          if trade["reason"] == "take_profit":
              emoji, label = "🎯", "TAKE PROFIT"
          else:
              emoji, label = "🛑", "STOP LOSS"
          sign = "+" if trade["pnl"] >= 0 else ""

          # Find the matching journal entry for agent attribution
          trade_journal = next(
              (j for j in journal if j.get("ticker") == trade["ticker"] and j.get("action") == "BUY"),
              {}
          )
          signals    = trade_journal.get("agent_signals", {})
          agent_line = ""
          if signals:
              aligned = []
              if "buy" in str(signals.get("fundamental", "")).lower():
                  aligned.append("Fund ✓")
              if any(k in str(signals.get("technical", "")).lower() for k in ("bullish", "buy")):
                  aligned.append("Tech ✓")
              if any(k in str(signals.get("sentiment", "")).lower() for k in ("positive", "bullish")):
                  aligned.append("Sent ✓")
              if signals.get("risk_approved"):
                  aligned.append("Risk ✓")
              if aligned:
                  agent_line = f"Agents: {', '.join(aligned)}\n"

          lines.append(
              f"{emoji} <b>{label} — {trade['ticker']}</b>\n"
              f"Entry: ${trade['entry_price']:.2f} → Exit: ${trade['exit_price']:.2f}\n"
              f"P&amp;L: {sign}${trade['pnl']:.2f} ({sign}{trade['pnl_pct']:.1f}%)\n"
              + agent_line
          )
  ```

- [ ] **Step 2: Apply the same agent attribution to the dead money (time_exits) loop**

  Find the time exits loop (around line 222):
  ```python
      # Dead money exits
      for trade in time_exits:
          sign = "+" if trade["pnl"] >= 0 else ""
          lines.append(
              f"⏳ <b>TIME EXIT ({trade.get('days_held', '?')}d) — {trade['ticker']}</b>\n"
              f"Entry: ${trade['entry_price']:.2f} → Exit: ${trade['exit_price']:.2f}\n"
              f"P&amp;L: {sign}${trade['pnl']:.2f} ({sign}{trade['pnl_pct']:.1f}%)  "
              f"<i>No follow-through — capital recycled</i>\n"
          )
  ```

  Replace with:
  ```python
      # Dead money exits
      for trade in time_exits:
          sign = "+" if trade["pnl"] >= 0 else ""
          trade_journal = next(
              (j for j in journal if j.get("ticker") == trade["ticker"] and j.get("action") == "BUY"),
              {}
          )
          signals    = trade_journal.get("agent_signals", {})
          agent_line = ""
          if signals:
              aligned = []
              if "buy" in str(signals.get("fundamental", "")).lower():
                  aligned.append("Fund ✓")
              if any(k in str(signals.get("technical", "")).lower() for k in ("bullish", "buy")):
                  aligned.append("Tech ✓")
              if any(k in str(signals.get("sentiment", "")).lower() for k in ("positive", "bullish")):
                  aligned.append("Sent ✓")
              if signals.get("risk_approved"):
                  aligned.append("Risk ✓")
              if aligned:
                  agent_line = f"Agents: {', '.join(aligned)}\n"
          lines.append(
              f"⏳ <b>TIME EXIT ({trade.get('days_held', '?')}d) — {trade['ticker']}</b>\n"
              f"Entry: ${trade['entry_price']:.2f} → Exit: ${trade['exit_price']:.2f}\n"
              f"P&amp;L: {sign}${trade['pnl']:.2f} ({sign}{trade['pnl_pct']:.1f}%)  "
              f"<i>No follow-through — capital recycled</i>\n"
              + agent_line
          )
  ```

  > Note: `journal` was already extracted from `portfolio` in the closed_trades loop above. Both loops share it — no need to re-declare.

- [ ] **Step 3: Verify `eod_session.py` is importable**

  ```bash
  cd "c:/Users/Fahad/OneDrive/Desktop/Claude Projects/Car Cleaning Gel Amazon/trading-system"
  python -c "import eod_session; print('eod_session imports OK')"
  ```

  Expected: `eod_session imports OK`

- [ ] **Step 4: Commit**

  ```bash
  git add eod_session.py
  git commit -m "feat: add agent performance attribution to EOD trade summaries"
  ```

---

## Task 6: Final Smoke Test

- [ ] **Step 1: Verify all five modified files import cleanly**

  ```bash
  cd "c:/Users/Fahad/OneDrive/Desktop/Claude Projects/Car Cleaning Gel Amazon/trading-system"
  python -c "
  import config
  import tools.paper_broker
  import tools.market_regime
  import morning_session
  import eod_session
  print('All modules import OK')

  # Confirm new config constants exist
  assert hasattr(config, 'SLIPPAGE_PCT')
  assert hasattr(config, 'TICKER_BETA')
  assert hasattr(config, 'MAX_PORTFOLIO_BETA')
  assert hasattr(config, 'VIX_ROC_THRESHOLD')

  # Confirm new market_regime functions exist
  from tools.market_regime import get_vix_roc, get_hyg_signal, had_earnings_recently
  roc, _ = get_vix_roc()
  risk_off, _, _ = get_hyg_signal()
  recent = had_earnings_recently('AAPL')

  # Confirm morning_session helper exists
  assert callable(morning_session._portfolio_beta)

  print('All assertions passed. System ready.')
  "
  ```

  Expected:
  ```
  All modules import OK
  All assertions passed. System ready.
  ```

- [ ] **Step 2: Push to remote**

  ```bash
  git pull --rebase
  git push
  ```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task covering it |
|-----------------|-----------------|
| Slippage 15bps on paper fills | Task 2 |
| `SLIPPAGE_PCT` in config | Task 1 |
| `TICKER_BETA` + `MAX_PORTFOLIO_BETA` in config | Task 1 |
| `_portfolio_beta()` helper | Task 4b |
| Beta cap check blocks trade + sends Telegram | Task 4e |
| Re-fetch price after approval | Task 4f |
| `analysis_price` vs `exec_price` logged in journal | Task 4f |
| `get_vix_roc()` | Task 3 |
| VIX RoC applies additional 0.5× multiplier | Task 4c |
| `get_hyg_signal()` | Task 3 |
| HYG risk-off caps sizing at 0.5× | Task 4c |
| HYG status in analysis Telegram message | Task 4c step 7 |
| `had_earnings_recently()` | Task 3 |
| Post-earnings 1-day backward block | Task 4d |
| Forward earnings check expanded to 5 days | Task 4d |
| `agent_signals` captured in journal entry | Task 4f |
| Agent attribution shown for TP/SL closes | Task 5 step 1 |
| Agent attribution shown for time exits | Task 5 step 2 |
| All new data fetches fail gracefully | Tasks 3, 4 — all use try/except with neutral fallback |

No gaps found.
