#!/usr/bin/env python3
"""
test_messages.py — Full trial run of all Telegram message paths.

Run from trading-system/:
    python test_messages.py

Mocks: market data (Finnhub/yfinance), LLM agents (litellm), Telegram API,
       yfinance earnings.  Uses real docs/portfolio.json for session state.

Prints every message that WOULD appear in Telegram, in order.
Any Python exception = a bug caught before it hits production.
"""
import sys
import os
import json
import re
import io

# Force UTF-8 output so emojis/box-drawing chars print on Windows terminals
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─────────────────────────────────────────────────────────────────────────────
# Pre-stub packages that fail on this local Anaconda env (numpy 1.x/2.x clash)
# These stubs prevent ImportError when technical_analyst tries to import pandas.
# The actual compute_indicators function is replaced before agents run.
# ─────────────────────────────────────────────────────────────────────────────
from unittest.mock import MagicMock

_PANDAS_MOCK = MagicMock()
_PANDAS_MOCK.DataFrame.return_value = MagicMock()
for _k in ("pandas", "pandas.core", "pandas.core.frame", "numexpr", "bottleneck"):
    if _k not in sys.modules:
        sys.modules[_k] = _PANDAS_MOCK

_TA_MOCK = MagicMock()
for _k in ("ta", "ta.trend", "ta.momentum", "ta.volatility", "ta.volume"):
    if _k not in sys.modules:
        sys.modules[_k] = _TA_MOCK

# Also stub numpy so technical_indicators.py doesn't crash on local Anaconda
import numpy as _numpy_real   # may succeed even if pandas fails
sys.modules.setdefault("numpy", _numpy_real)

# Stub tools.technical_indicators entirely — compute_indicators returns realistic fixture
_TI_STUB = MagicMock()
_TI_STUB.compute_indicators.return_value = {
    "current_close": 875.30,
    "trend": {"ema_20": 855.10, "ema_50": 834.20, "sma_20": 852.40,
              "price_vs_ema20": 2.36, "price_vs_ema50": 4.93},
    "momentum": {"rsi_14": 61.2, "rsi_signal": "neutral"},
    "macd": {"macd_line": 12.45, "signal_line": 8.20, "histogram": 4.25, "crossover": "bullish"},
    "bollinger_bands": {"upper": 920.0, "middle": 852.40, "lower": 784.80,
                        "bandwidth": 15.9, "price_position": "near_upper"},
    "volume": {"current_volume": 42_000_000, "avg_volume_20d": 32_000_000, "volume_ratio": 1.31},
    "volatility": {"atr_14": 18.50, "atr_pct": 2.11},
}
sys.modules["tools.technical_indicators"] = _TI_STUB

# Must run from trading-system/ so relative paths (.tmp/, docs/) resolve
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

# ─────────────────────────────────────────────────────────────────────────────
# Backup and restore portfolio.json so the test never corrupts real session state
# ─────────────────────────────────────────────────────────────────────────────
import atexit
import shutil

_PORTFOLIO_FILE = os.path.join("docs", "portfolio.json")
_PORTFOLIO_BACKUP = _PORTFOLIO_FILE + ".test_backup"

if os.path.exists(_PORTFOLIO_FILE):
    shutil.copy2(_PORTFOLIO_FILE, _PORTFOLIO_BACKUP)
    print(f"[test] Portfolio backed up → {_PORTFOLIO_BACKUP}")

def _restore_portfolio():
    if os.path.exists(_PORTFOLIO_BACKUP):
        shutil.copy2(_PORTFOLIO_BACKUP, _PORTFOLIO_FILE)
        os.remove(_PORTFOLIO_BACKUP)
        print(f"\n[test] Portfolio restored from backup.")

atexit.register(_restore_portfolio)

# ─────────────────────────────────────────────────────────────────────────────
# Capture store
# ─────────────────────────────────────────────────────────────────────────────

MESSAGES: list[tuple[str, object]] = []   # (kind, content)

def _capture_send_message(text: str) -> dict:
    MESSAGES.append(("message", text))
    return {"ok": True}

def _capture_approval(summary: dict) -> int:
    MESSAGES.append(("approval_card", summary))
    return 99999

def _mock_poll(*_, **__) -> str:
    return "approved"   # always approve so we see the execution confirmation message

# ─────────────────────────────────────────────────────────────────────────────
# Mock market data
# ─────────────────────────────────────────────────────────────────────────────

PRICES = {
    "AAPL": 210.45, "GOOGL": 334.21, "NVDA": 875.30, "MSFT": 415.90,
    "AMZN": 195.60, "META": 535.80, "LLY": 820.15, "JPM": 242.30,
    "XOM": 115.70, "CAT": 340.25, "WMT": 94.50,  "FCX": 42.30,
    "NEE": 68.90,  "PLD": 110.45,
    "BTC-USD": 87500.00, "ETH-USD": 3200.00, "SOL-USD": 145.00,
    "SPY": 545.30, "QQQ": 445.80, "^VIX": 19.1,
    # Sector ETFs
    "XLK": 220.50, "XLC": 88.90,  "XLY": 195.30, "XLV": 146.80,
    "XLF": 43.20,  "XLE": 93.40,  "XLI": 130.60, "XLP": 77.20,
    "XLB": 87.40,  "XLU": 68.30,  "XLRE": 37.80,
}

def _mock_price(ticker: str) -> float:
    if ticker in PRICES:
        return PRICES[ticker]
    raise ValueError(f"No mock price for {ticker}")

def _mock_ohlcv(ticker: str, start: str, end: str, **_) -> list:
    """Return 70 bars of synthetic OHLCV.  Last bar == PRICES[ticker]."""
    import random
    rng = random.Random(abs(hash(ticker)) % 100_000)
    base = PRICES.get(ticker, 100.0)
    price = base * 0.88
    bars = []
    for i in range(70):
        pct = rng.uniform(-0.018, 0.020)
        price *= (1 + pct)
        vol = rng.randint(5_000_000, 50_000_000)
        if "-USD" in ticker:
            vol = rng.randint(20_000, 200_000)
        bars.append({
            "date":   f"2026-02-{(i % 28) + 1:02d}",
            "open":   round(price * 0.999, 4),
            "high":   round(price * 1.010, 4),
            "low":    round(price * 0.990, 4),
            "close":  round(price, 4),
            "volume": vol,
        })
    bars[-1]["close"] = base   # pin last close to mock price
    return bars

def _mock_no_earnings(ticker: str, days: int = 5) -> dict:
    return {"has_earnings": False, "date": None, "days_until": None}

# ─────────────────────────────────────────────────────────────────────────────
# Mock LLM (litellm.completion)
# ─────────────────────────────────────────────────────────────────────────────

from unittest.mock import MagicMock

def _mock_llm(**kwargs):
    model = kwargs.get("model", "")
    msgs  = kwargs.get("messages", [])
    system_text = next((m["content"] for m in msgs if m["role"] == "system"), "").lower()

    # ── Fund Manager (Opus) ──────────────────────────────────────────────
    if "claude-opus" in model:
        raw = json.dumps({
            "action": "buy",
            "ticker": "NVDA",          # orchestrator overwrites this with real ticker
            "qty": 1,
            "position_size_pct": 0.20,
            "stop_loss_pct": 0.05,
            "override": False,
            "final_reasoning": (
                "Strong AI-infrastructure demand backs near-term earnings beats. "
                "Entry with 5% stop and 10% target gives 2:1 R/R within portfolio risk limits. "
                "Sized to 1 share to stay within 25% single-position cap."
            ),
        })

    # ── Trader (Sonnet) ──────────────────────────────────────────────────
    elif "claude-sonnet" in model:
        raw = json.dumps({
            "action": "buy",
            "conviction": "high",
            "position_size": 0.10,
            "entry_price_target": None,
            "stop_loss_pct": 0.05,
            "reasoning": (
                "MACD bullish crossover with above-average volume confirms uptrend. "
                "Fundamental valuation discount and analyst upgrades add conviction. "
                "RSI at 61 leaves room to run before overbought territory."
            ),
        })

    # ── Risk Manager perspectives (Groq, via system prompt keywords) ────
    elif "risk seeking" in system_text or "risk_seeking" in " ".join(
            m.get("content","") for m in msgs).lower():
        raw = json.dumps({
            "perspective": "risk_seeking",
            "assessment": "approve",
            "recommended_position_size": 0.15,
            "rationale": "Strong multi-signal confluence justifies maximum allocation.",
        })
    elif "risk_conservative" in system_text or "prioritize capital" in system_text:
        raw = json.dumps({
            "perspective": "risk_conservative",
            "assessment": "reduce",
            "recommended_position_size": 0.05,
            "rationale": "VIX elevated; reduce size to protect downside.",
        })
    elif "facilitator" in system_text or "synthesize" in system_text:
        raw = json.dumps({
            "action": "buy",
            "final_position_size": 0.10,
            "stop_loss_pct": 0.05,
            "risk_assessment": "approved",
            "reasoning": (
                "Two perspectives approved, one recommended reduction. "
                "Moderate 10% size balances upside capture with downside protection."
            ),
        })

    # ── Bull / Bear Researchers (GPT-4o-mini) ───────────────────────────
    elif "bullish equity researcher" in system_text:
        raw = (
            "Strong AI datacenter growth cycle driving multi-year EPS expansion. "
            "Market share 80%+ in GPU training; now expanding into inference. "
            "Top bull catalysts: (1) Blackwell ramp in Q2, "
            "(2) sovereign-AI government deals, (3) margin expansion to 76%+."
        )
    elif "bearish equity researcher" in system_text:
        raw = (
            "Valuation at 35x forward earnings prices in a flawless execution. "
            "China export restrictions threaten $4B revenue and could widen. "
            "Top bear risks: (1) Blackwell supply-chain delays, "
            "(2) AMD MI300X share gains in inference, (3) hyperscaler capex cuts."
        )

    # ── Fundamental Analyst (Groq) ───────────────────────────────────────
    elif "fundamental" in system_text:
        raw = (
            "Q4 revenue $44.1B (+122% YoY), EPS $5.16 (+152%). Beats consensus by 8%. "
            "Data center $36.2B (+217%). Gross margin 73.5%. P/E 35x forward — premium "
            "justified by 90%+ EPS CAGR. Balance sheet: $26B cash, $9B debt. ROE 91%."
        )

    # ── Sentiment Analyst (Groq) ─────────────────────────────────────────
    elif "sentiment" in system_text:
        raw = (
            "Bullish. Analyst: 48 BUY / 3 HOLD / 0 SELL. Median PT +9%. "
            "Reddit/Twitter: 78% positive. Options P/C ratio 0.62 (bullish). "
            "News last 7d: 12 positive, 2 neutral, 1 negative. No insider selling."
        )

    # ── Technical Analyst (Groq) ─────────────────────────────────────────
    elif "technical" in system_text:
        raw = (
            "Price above MA50 and MA200. MACD bullish cross 3 days ago. "
            "RSI 61 — room to run. Volume 3d avg +28% above 20d avg. "
            "Bull flag on 4H chart. Support $845 (MA50), resistance $920 (ATH)."
        )

    # ── Neutral risk perspective / fallback ──────────────────────────────
    elif "neutral" in system_text and "perspective" in system_text:
        raw = json.dumps({
            "perspective": "neutral",
            "assessment": "approve",
            "recommended_position_size": 0.10,
            "rationale": "Balanced risk/reward. Standard allocation appropriate.",
        })
    else:
        raw = json.dumps({
            "action": "hold",
            "conviction": "low",
            "position_size": 0.0,
            "stop_loss_pct": 0.03,
            "reasoning": "Insufficient signal.",
        })

    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = raw
    return resp

# ─────────────────────────────────────────────────────────────────────────────
# Apply all patches BEFORE importing any session scripts
# ─────────────────────────────────────────────────────────────────────────────

import tools.market_data as _md
_md.get_latest_price = _mock_price
_md.get_ohlcv        = _mock_ohlcv

import tools.market_regime as _mr
_mr.get_latest_price = _mock_price
_mr.get_ohlcv        = _mock_ohlcv
_mr.has_earnings_soon = _mock_no_earnings

import tools.sector_analysis as _sa
_sa.get_latest_price = _mock_price
_sa.get_ohlcv        = _mock_ohlcv

import litellm as _ll
_ll.completion = _mock_llm

import tools.telegram_bot as _tg
_tg.send_message          = _capture_send_message
_tg.send_approval_request = _capture_approval
_tg.poll_for_response     = _mock_poll

# ─────────────────────────────────────────────────────────────────────────────
# Import session scripts (after patches are live)
# ─────────────────────────────────────────────────────────────────────────────

import premarket_check
import morning_session
import midday_check
import preclose_alert
import eod_session
import weekly_briefing

# Fix local names that were bound at import time (before patches were applied)
for _mod in (premarket_check, morning_session, midday_check,
             preclose_alert, eod_session, weekly_briefing):
    if hasattr(_mod, "get_latest_price"): _mod.get_latest_price = _mock_price
    if hasattr(_mod, "get_ohlcv"):        _mod.get_ohlcv        = _mock_ohlcv
    if hasattr(_mod, "send_message"):     _mod.send_message     = _capture_send_message
    if hasattr(_mod, "send_approval_request"): _mod.send_approval_request = _capture_approval
    if hasattr(_mod, "poll_for_response"): _mod.poll_for_response = _mock_poll

# morning_session also imports run_pipeline from orchestrator
import orchestrator as _orch
import agents.fundamental_analyst as _fa
import agents.sentiment_analyst   as _sa2
import agents.technical_analyst   as _ta
import agents.researcher          as _res
import agents.trader              as _tr
import agents.risk_manager        as _rm
import agents.fund_manager        as _fm

for _agent in (_fa, _sa2, _ta, _res, _tr, _rm, _fm):
    import litellm as _ll2
    _agent_ll = __import__("litellm")
    _agent_ll.completion = _mock_llm

# ─────────────────────────────────────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────────────────────────────────────

_HTML_STRIP = re.compile(r"<[^>]+>")

def _strip(text: str) -> str:
    return _HTML_STRIP.sub("", text).strip()

def _show(label: str, content: object) -> None:
    bar  = "═" * 68
    dash = "─" * 68
    print(f"\n{bar}")
    print(f"  📱  {label}")
    print(dash)
    if isinstance(content, str):
        print(_strip(content))
    elif isinstance(content, dict):
        # Approval card
        s = content
        tp_sign = "+"
        tp_pct  = f"{tp_sign}{s.get('take_profit_pct', 0):.1f}%"
        sl_pct  = f"-{s.get('stop_loss_pct', 0):.1f}%"
        lines = [
            f"TRADE OPPORTUNITY — Day {s.get('session_day')}/{s.get('total_days')}",
            f"",
            f"Ticker:     {s.get('ticker')} @ ${s.get('current_price', 0):.2f}",
            f"Conviction: {str(s.get('conviction','')).upper()}",
            f"",
            f"WHY: {_strip(str(s.get('why', '')))}",
            f"",
            f"BULL: {_strip(str(s.get('bull_case', '')))}",
            f"BEAR: {_strip(str(s.get('bear_case', '')))}",
            f"",
            f"Entry: ${s.get('current_price', 0):.2f}",
            f"TP:    ${s.get('take_profit', 0):.2f}  ({tp_pct})",
            f"SL:    ${s.get('stop_loss', 0):.2f}  ({sl_pct})",
            f"Size:  ${s.get('position_size_usd', 0):.0f}  ({s.get('qty')} shares)",
        ]
        if s.get("vix_label"):
            lines.append(f"VIX: {s['vix_label']}")
        for ln in lines:
            print(ln)
    print(dash)

def _flush(section: str) -> None:
    if not MESSAGES:
        print("  [no messages sent]")
        return
    for kind, content in MESSAGES:
        label = f"send_message" if kind == "message" else "send_approval_request"
        _show(f"{section} — {label}", content)
    MESSAGES.clear()

# ─────────────────────────────────────────────────────────────────────────────
# Run each session
# ─────────────────────────────────────────────────────────────────────────────

ERRORS: list[str] = []

def _run(name: str, fn) -> None:
    print(f"\n\n{'#'*68}")
    print(f"#  RUNNING: {name}")
    print(f"{'#'*68}")
    try:
        fn()
        _flush(name)
    except Exception as exc:
        import traceback
        msg = traceback.format_exc()
        ERRORS.append(f"{name}: {msg}")
        print(f"\n  ❌  EXCEPTION in {name}:\n{msg}")


# ── 1. Pre-Market Gap Scanner ─────────────────────────────────────────────────
_run("Pre-Market (7:00 AM ET)", premarket_check.main)

# ── 2. Morning Session ────────────────────────────────────────────────────────
_run("Morning Session (7:30 AM ET)", morning_session.main)

# ── 3. Midday Monitor ────────────────────────────────────────────────────────
_run("Midday Check (12:00 PM ET)", midday_check.main)

# ── 4. Pre-Close Alert ───────────────────────────────────────────────────────
_run("Pre-Close Alert (3:30 PM ET)", preclose_alert.main)

# ── 5. End-of-Day Session ────────────────────────────────────────────────────
_run("EOD Session (4:15 PM ET)", eod_session.main)

# ── 6. Weekly Briefing ───────────────────────────────────────────────────────
_run("Weekly Briefing (Sunday 6 PM ET)", weekly_briefing.main)

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\n\n{'#'*68}")
print("# TEST RUN COMPLETE")
print(f"{'#'*68}")
if ERRORS:
    print(f"\n❌  {len(ERRORS)} ERRORS FOUND:")
    for e in ERRORS:
        print(f"\n  • {e}")
else:
    print("\n✅  All session scripts ran without exceptions.")
    print("    Review messages above for content / formatting issues.")
