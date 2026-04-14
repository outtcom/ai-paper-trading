"""
Researcher Agents: Bull and Bear
Model: claude-sonnet-4-6
Two agents debate the stock using analyst reports.
Bull argues for upside; Bear argues for downside.
They run N debate rounds to stress-test the thesis.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic
from config import MODELS, RESEARCHER_DEBATE_ROUNDS
from tools.state_manager import save_state, write_log, log_error

client = anthropic.Anthropic()

BULL_SYSTEM = """You are a bullish equity researcher at a trading firm.
Your job is to make the strongest possible case FOR buying a stock.
Use the fundamental, sentiment, and technical reports as evidence.
Be rigorous — acknowledge risks but explain why the upside outweighs them.
Keep your argument concise (200-300 words). End with your top 3 bull catalysts."""

BEAR_SYSTEM = """You are a bearish equity researcher at a trading firm.
Your job is to make the strongest possible case AGAINST buying a stock (or for selling).
Use the fundamental, sentiment, and technical reports as evidence.
Be rigorous — acknowledge positives but explain why the risks outweigh them.
Keep your argument concise (200-300 words). End with your top 3 bear risks."""


def _build_context(state: dict) -> str:
    return f"""Ticker: {state['ticker']} | Date: {state['date']}

FUNDAMENTAL ANALYSIS:
{state.get('fundamental_report', 'N/A')}

SENTIMENT ANALYSIS:
{state.get('sentiment_report', 'N/A')}

TECHNICAL ANALYSIS:
{state.get('technical_report', 'N/A')}"""


def run(state: dict) -> dict:
    """Run bull/bear debate and update state with bull_case and bear_case."""
    ticker = state["ticker"]
    date = state["date"]

    try:
        context = _build_context(state)
        bull_messages = [{"role": "user", "content": f"{context}\n\nMake your bull case for {ticker}."}]
        bear_messages = [{"role": "user", "content": f"{context}\n\nMake your bear case for {ticker}."}]

        for round_num in range(RESEARCHER_DEBATE_ROUNDS):
            # Bull argues
            bull_response = client.messages.create(
                model=MODELS["analyst"],
                max_tokens=600,
                system=BULL_SYSTEM,
                messages=bull_messages,
            )
            bull_argument = bull_response.content[0].text
            bull_messages.append({"role": "assistant", "content": bull_argument})

            # Bear responds to bull's argument
            bear_messages.append({
                "role": "user",
                "content": f"The bull researcher argues:\n{bull_argument}\n\nRespond with your counter-argument."
            })
            bear_response = client.messages.create(
                model=MODELS["analyst"],
                max_tokens=600,
                system=BEAR_SYSTEM,
                messages=bear_messages,
            )
            bear_argument = bear_response.content[0].text
            bear_messages.append({"role": "assistant", "content": bear_argument})

            # Bull responds to bear
            if round_num < RESEARCHER_DEBATE_ROUNDS - 1:
                bull_messages.append({
                    "role": "user",
                    "content": f"The bear researcher counters:\n{bear_argument}\n\nReinforce your bull case."
                })

        # Final summaries
        final_bull = bull_messages[-1]["content"] if bull_messages[-1]["role"] == "assistant" else bull_argument
        final_bear = bear_messages[-1]["content"] if bear_messages[-1]["role"] == "assistant" else bear_argument

        state["bull_case"] = final_bull
        state["bear_case"] = final_bear

        write_log(ticker, date, f"[BULL RESEARCHER - FINAL]\n{final_bull}\n\n[BEAR RESEARCHER - FINAL]\n{final_bear}")
        save_state(state)

    except Exception as e:
        state = log_error(state, "researcher", str(e))
        state["bull_case"] = f"Bull research unavailable: {e}"
        state["bear_case"] = f"Bear research unavailable: {e}"

    return state
