"""
Technical Analyst Agent
Model: groq/llama-3.1-70b-versatile (via LiteLLM)
Interprets technical indicators (RSI, MACD, Bollinger Bands, EMA, volume, ATR)
to produce a technical analysis report with price action context.
"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm
from config import MODELS, TECHNICAL_INDICATOR_PERIOD
from tools.market_data import get_ohlcv
from tools.technical_indicators import compute_indicators
from tools.state_manager import save_state, write_log, log_error
from datetime import datetime, timedelta

SYSTEM_PROMPT = """You are a senior technical analyst at a trading firm.
Your job is to interpret technical indicators and price action to assess
the near-term technical setup of a stock.

Be specific about what the indicators are telling you.
Look for confluence between signals.

Format your report in these sections:
1. Trend Analysis (EMA 20/50 relationship, price position)
2. Momentum (RSI reading and what it signals)
3. MACD Signal (direction, crossover status)
4. Bollinger Bands (price position, bandwidth)
5. Volume Analysis (above/below average, confirming or diverging)
6. Volatility (ATR context)
7. Technical Verdict: BULLISH/BEARISH/NEUTRAL + confidence (low/medium/high) + key support/resistance levels
"""


def run(state: dict) -> dict:
    """Run technical analysis and update state with technical_report."""
    ticker = state["ticker"]
    date = state["date"]

    try:
        end = date
        start = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=TECHNICAL_INDICATOR_PERIOD * 2)).strftime("%Y-%m-%d")
        bars = get_ohlcv(ticker, start, end)
        indicators = compute_indicators(bars[-TECHNICAL_INDICATOR_PERIOD:] if len(bars) >= TECHNICAL_INDICATOR_PERIOD else bars)

        # Also pass last 10 days of price for context
        recent_prices = [{"date": b["date"], "close": b["close"], "volume": b["volume"]} for b in bars[-10:]]

        user_content = f"""Perform technical analysis for {ticker} as of {date}.

Technical Indicators (computed from last {min(len(bars), TECHNICAL_INDICATOR_PERIOD)} trading days):
{json.dumps(indicators, indent=2)}

Recent 10-Day Price Action:
{json.dumps(recent_prices, indent=2)}

Produce your technical analysis report."""

        response = litellm.completion(
            model=MODELS["fast"],
            max_tokens=1200,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )

        report = response.choices[0].message.content
        state["technical_report"] = report
        write_log(ticker, date, f"[TECHNICAL ANALYST]\n{report}")
        save_state(state)

    except Exception as e:
        state = log_error(state, "technical_analyst", str(e))
        state["technical_report"] = f"Technical analysis unavailable: {e}"

    return state
