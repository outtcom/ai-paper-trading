"""
Fundamental Analyst Agent
Model: claude-sonnet-4-6
Analyzes financial statements, earnings, key ratios, and company profile
to produce a fundamental analysis report.
"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic
from config import MODELS
from tools.finnhub_data import get_financials, get_company_profile, get_insider_transactions
from tools.state_manager import save_state, write_log, log_error

client = anthropic.Anthropic()

SYSTEM_PROMPT = """You are a senior fundamental analyst at a trading firm.
Your job is to analyze a company's financial health and produce a concise,
evidence-based fundamental analysis report.

Be objective. Cite specific numbers. Conclude with a clear fundamental outlook:
BULLISH, BEARISH, or NEUTRAL, with your confidence level (low/medium/high).

Format your report in these sections:
1. Company Overview
2. Financial Health (profitability, leverage, liquidity)
3. Valuation
4. Insider Activity
5. Fundamental Verdict (BULLISH/BEARISH/NEUTRAL + confidence + key reasons)
"""


def run(state: dict) -> dict:
    """Run fundamental analysis and update state with fundamental_report."""
    ticker = state["ticker"]
    date = state["date"]

    try:
        financials = get_financials(ticker)
        profile = get_company_profile(ticker)
        insiders = get_insider_transactions(ticker)

        user_content = f"""Analyze {ticker} as of {date}.

Company Profile:
{json.dumps(profile, indent=2)}

Financial Metrics:
{json.dumps(financials, indent=2)}

Recent Insider Transactions (last 10):
{json.dumps(insiders, indent=2)}

Produce your fundamental analysis report."""

        response = client.messages.create(
            model=MODELS["analyst"],
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )

        report = response.content[0].text
        state["fundamental_report"] = report
        write_log(ticker, date, f"[FUNDAMENTAL ANALYST]\n{report}")
        save_state(state)

    except Exception as e:
        state = log_error(state, "fundamental_analyst", str(e))
        state["fundamental_report"] = f"Fundamental analysis unavailable: {e}"

    return state
