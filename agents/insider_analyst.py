"""
Insider Activity + Political Signal Analyst
Model: groq/llama-3.3-70b-versatile (MODELS["fast"])

Analyzes SEC Form 4 insider transactions and Trump/political stock mentions.
Inserted between Technical Analyst (step 3) and Researcher Debate (step 4).

Loads pre-computed signals from .tmp/state/YYYY-MM-DD/insider_signals.json
when available (written by insider_scan.py at 7:30 AM ET). Falls back to
live computation if the file is missing.
"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm
from config import MODELS
from tools.insider_tracker import compute_insider_signal
from tools.political_signal import get_trump_mentions
from tools.state_manager import save_state, write_log, log_error

SYSTEM_PROMPT = """You are a senior insider activity and political signals analyst at a trading firm.

Analyze SEC Form 4 insider transactions and political/Trump stock mentions to assess their likely market impact.

Key principles:
- C-suite buys (CEO, CFO, COO, President) carry more weight than director sales
- Dollar magnitude matters: >$500K net buying = strong conviction signal
- Cluster patterns: 3+ insiders buying simultaneously is rarely coincidence
- Zero-price transactions (RSU vests, option exercises) are routine — do not over-weight them
- Trump/political mentions: assess if the statement is positive or negative for the stock and estimate near-term price impact direction
- Routine compensation-related activity (scheduled option exercises, 10b5-1 plans) is lower signal

Format your report in exactly these sections:
1. Insider Transaction Summary (who bought/sold, dollar amounts, dates — cite specifics)
2. Signal Assessment (is this meaningful conviction or routine activity?)
3. Political / Trump Mentions (if any — what was said and directional impact; write "None detected" if absent)
4. Verdict: BULLISH / BEARISH / NEUTRAL — confidence (low/medium/high) — one-sentence key reason

Be concise. Maximum 350 words."""


def run(state: dict) -> dict:
    """Run insider activity and political signal analysis; update state."""
    ticker = state["ticker"]
    date = state["date"]

    try:
        # Load pre-computed signals — docs/ (GHA committed cache) takes priority,
        # then .tmp/ (local dev same-runner), then compute live as fallback.
        docs_path = os.path.join("docs", "insider_signals.json")
        tmp_path  = os.path.join(".tmp", "state", date, "insider_signals.json")
        insider_signal = None
        trump_mentions = []

        if os.path.exists(docs_path):
            with open(docs_path, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("date") == date:
                ticker_data    = cached.get("signals", {}).get(ticker, {})
                insider_signal = ticker_data.get("insider_signal")
                trump_mentions = ticker_data.get("trump_mentions", [])
                source_note    = "pre-computed by scanner (docs/)"
            else:
                source_note = "docs/ cache stale — computing live"
        elif os.path.exists(tmp_path):
            with open(tmp_path, encoding="utf-8") as f:
                all_signals    = json.load(f)
            ticker_data    = all_signals.get(ticker, {})
            insider_signal = ticker_data.get("insider_signal")
            trump_mentions = ticker_data.get("trump_mentions", [])
            source_note    = "pre-computed by scanner (.tmp/)"
        else:
            source_note = "computed live (no cache found)"

        if insider_signal is None:
            insider_signal = compute_insider_signal(ticker)
            trump_mentions = get_trump_mentions(ticker, days_back=7)

        trump_summary = trump_mentions[:5] if trump_mentions else []
        trump_text = json.dumps(trump_summary, indent=2) if trump_summary else "None detected in last 7 days."

        user_content = f"""Analyze insider activity and political signals for {ticker} as of {date}.

Insider Signal (source: {source_note}):
{json.dumps(insider_signal, indent=2)}

Trump / Political News Mentions:
{trump_text}

Produce your insider activity and political signal analysis report."""

        response = litellm.completion(
            model=MODELS["fast"],
            max_tokens=600,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )

        report = response.choices[0].message.content
        state["insider_report"] = report

        # Derive structured signal for downstream agents
        report_lower = report.lower()
        if "bullish" in report_lower:
            direction = "bullish"
        elif "bearish" in report_lower:
            direction = "bearish"
        else:
            direction = "neutral"

        trump_sentiments = [m.get("sentiment_hint", "neutral") for m in trump_mentions]
        if "positive" in trump_sentiments:
            trump_sentiment = "positive"
        elif "negative" in trump_sentiments:
            trump_sentiment = "negative"
        elif trump_mentions:
            trump_sentiment = "neutral"
        else:
            trump_sentiment = "none"

        state["insider_signal"] = {
            "direction": direction,
            "strength": insider_signal.get("signal_strength", "neutral"),
            "trump_mentions": len(trump_mentions),
            "trump_sentiment": trump_sentiment,
            "net_dollar_value": insider_signal.get("net_dollar_value", 0),
        }

        write_log(ticker, date, f"[INSIDER ANALYST]\n{report}")
        save_state(state)

    except Exception as e:
        state = log_error(state, "insider_analyst", str(e))
        state["insider_report"] = f"Insider analysis unavailable: {e}"
        state["insider_signal"] = {
            "direction": "neutral", "strength": "neutral",
            "trump_mentions": 0, "trump_sentiment": "none", "net_dollar_value": 0,
        }

    return state
