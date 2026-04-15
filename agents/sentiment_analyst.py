"""
Sentiment Analyst Agent
Model: groq/llama-3.1-70b-versatile (via LiteLLM)
Analyzes news headlines and Reddit social sentiment to produce a sentiment report.
"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm
from config import MODELS, NEWS_LOOKBACK_DAYS, REDDIT_POST_LIMIT
from tools.finnhub_data import get_news
from tools.reddit_sentiment import get_sentiment_summary
from tools.state_manager import save_state, write_log, log_error

SYSTEM_PROMPT = """You are a senior sentiment analyst at a trading firm.
Your job is to analyze news articles and social media sentiment for a stock
and produce a concise sentiment analysis report.

Focus on: material news events, sentiment trends, and market narrative.
Be objective. Distinguish noise from signal.

Format your report in these sections:
1. Key News Events (top 3-5 material items)
2. Social Media Sentiment (Reddit/social tone and key themes)
3. Sentiment Trend (improving/deteriorating/stable)
4. Sentiment Verdict: BULLISH/BEARISH/NEUTRAL + confidence (low/medium/high) + 2-3 key reasons
"""


def run(state: dict) -> dict:
    """Run sentiment analysis and update state with sentiment_report."""
    ticker = state["ticker"]
    date = state["date"]

    try:
        news = get_news(ticker, days_back=NEWS_LOOKBACK_DAYS)
        reddit = get_sentiment_summary(ticker, limit=REDDIT_POST_LIMIT)

        user_content = f"""Analyze sentiment for {ticker} as of {date}.

Recent News Articles ({len(news)} articles, last {NEWS_LOOKBACK_DAYS} days):
{json.dumps(news, indent=2)}

Reddit Social Sentiment:
{json.dumps(reddit, indent=2)}

Produce your sentiment analysis report."""

        response = litellm.completion(
            model=MODELS["fast"],
            max_tokens=1200,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )

        report = response.choices[0].message.content
        state["sentiment_report"] = report
        write_log(ticker, date, f"[SENTIMENT ANALYST]\n{report}")
        save_state(state)

    except Exception as e:
        state = log_error(state, "sentiment_analyst", str(e))
        state["sentiment_report"] = f"Sentiment analysis unavailable: {e}"

    return state
