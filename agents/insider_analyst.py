"""
Insider Activity + Political Signal Analyst
Model: groq/llama-3.3-70b-versatile (MODELS["fast"])

Analyzes SEC Form 4 insider transactions and Trump/political stock mentions.
Inserted between Technical Analyst (step 3) and Researcher Debate (step 4).

Loads pre-computed signals from docs/insider_signals.json (committed to repo
by insider_scan.py at 7:30 AM ET). Falls back to live computation if missing.
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

SYSTEM_PROMPT = """You are a senior insider activity analyst at a trading firm. Your job is to interpret SEC Form 4 filings and political news to help the trading team decide whether insider activity is a real signal or just routine noise.

WHAT ACTUALLY MATTERS — read this carefully:

INSIDER BUYS are high-signal when:
- An executive (CEO, CFO, COO, President) spends their own money on the open market
- Multiple insiders buy within a short window (cluster buy = rarely coincidence)
- The purchase is large relative to typical insider activity at this company
- Buying is happening near 52-week lows

INSIDER SELLS are usually LOW-signal because:
- Most large sells are pre-scheduled 10b5-1 plans (the insider filed the plan months ago and has no discretion over timing)
- Routine diversification — executives hold too much of one stock and must sell for tax/financial planning reasons
- Option exercises followed by immediate sale are almost always automatic — not a bearish view
- ONLY flag sells as truly bearish if: (a) the CEO/CFO specifically sells large blocks at market prices NOT at round/exact figures, or (b) multiple C-suite members sell simultaneously with no scheduled plan context

COMPENSATION GRANTS (zero-price transactions): these are RSU vests and stock awards — they are routine compensation. They appear in the data but should not be interpreted as bullish or bearish signals.

TRUMP / POLITICAL MENTIONS: only include if the article headline is specifically about THIS company and the political/tariff event. Ignore general market articles that just happen to mention Trump.

Write your analysis as a clear, plain-English summary that a portfolio manager can read in 30 seconds. Do NOT list raw numbers like "shares: 14163, price: 214.00" — translate them into human-readable analysis.

Format:
**Insider Activity:** [2-3 sentences: who did what, how much, and whether this looks like conviction or routine]
**Political / Trump Signal:** [1-2 sentences, or "None this week." if no relevant mentions]
**Verdict:** BULLISH / BEARISH / NEUTRAL — [one sentence explaining why]"""


def _format_signal_for_llm(insider_signal: dict, trump_mentions: list) -> str:
    """Build a human-readable summary of the raw signal data for the LLM."""
    sig = insider_signal or {}
    ticker = sig.get("ticker", "?")
    days = sig.get("days_back", 60)
    buys = sig.get("open_market_buys", 0)
    sells = sig.get("open_market_sells", 0)
    grants = sig.get("compensation_grants", 0)
    net_fmt = sig.get("net_open_market_fmt", "$0")
    strength = sig.get("signal_strength", "neutral")
    notable = sig.get("notable_transactions", [])
    largest = sig.get("largest_single_transaction")
    error = sig.get("error")

    if error:
        lines = [f"Data error for {ticker}: {error}"]
    else:
        lines = [
            f"Open-market transactions in last {days} days: {buys} buy(s), {sells} sell(s)",
            f"Compensation grants (RSU/options, excluded from signal): {grants}",
            f"Net open-market value: {net_fmt}  →  Signal: {strength.upper().replace('_', ' ')}",
        ]
        if notable:
            lines.append("Notable open-market transactions:")
            for n in notable[:5]:
                lines.append(
                    f"  • {n['name']} — {n['direction'].upper()} {n['dollar_fmt']} "
                    f"({n['shares_traded']:,} shares @ ${n['price_per_share']:.2f}) on {n['date']}"
                )
        elif buys + sells == 0:
            lines.append("No open-market transactions found in this period.")

    if trump_mentions:
        lines.append("")
        lines.append("Political / Trump headlines (this week, headline-matched only):")
        for m in trump_mentions[:4]:
            lines.append(f"  [{m['sentiment_hint'].upper()}] {m['headline']} ({m['date'][:10]})")
    else:
        lines.append("")
        lines.append("Political / Trump mentions: None this week.")

    return "\n".join(lines)


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

        formatted_data = _format_signal_for_llm(insider_signal, trump_mentions)

        user_content = f"""Write an insider activity analysis for {ticker} as of {date}.

{formatted_data}

Data source: {source_note}"""

        _fast_kwargs = dict(
            model=MODELS["fast"],
            max_tokens=400,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
        # gpt-oss is a reasoning model — default effort can burn the whole
        # max_tokens budget on hidden reasoning and return empty content.
        # See tools/groq_quota.py::groq_completion for the same fix.
        if "gpt-oss" in MODELS["fast"]:
            _fast_kwargs["reasoning_effort"] = "low"
        response = litellm.completion(**_fast_kwargs)

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
            "net_open_market_value": insider_signal.get("net_open_market_value", 0),
        }

        write_log(ticker, date, f"[INSIDER ANALYST]\n{report}")
        save_state(state)

    except Exception as e:
        state = log_error(state, "insider_analyst", str(e))
        state["insider_report"] = f"Insider analysis unavailable: {e}"
        state["insider_signal"] = {
            "direction": "neutral", "strength": "neutral",
            "trump_mentions": 0, "trump_sentiment": "none", "net_open_market_value": 0,
        }

    return state
