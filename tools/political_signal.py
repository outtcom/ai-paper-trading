"""
Detect Trump and political mentions in Finnhub company news.
No LLM calls — keyword filtering on headline + summary.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from tools.finnhub_data import get_news

POLITICAL_KEYWORDS = [
    "trump", "tariff", "trade deal", "executive order",
    "white house", "sanctions", "president",
]

_POSITIVE_WORDS = {"deal", "boost", "win", "surge", "lifted", "rally", "exemption", "approved", "agreement"}
_NEGATIVE_WORDS = {"ban", "tariff", "sanction", "risk", "threat", "cut", "drop", "hit", "concern", "fear", "penalty"}


def _sentiment_hint(text: str) -> str:
    words = set(text.lower().split())
    if words & _POSITIVE_WORDS:
        return "positive"
    if words & _NEGATIVE_WORDS:
        return "negative"
    return "neutral"


def get_trump_mentions(ticker: str, days_back: int = 7) -> list:
    """
    Return news articles where Trump/political keywords appear in the HEADLINE.
    Headline-only matching eliminates general market articles that tangentially
    mention the keyword in body text but aren't actually about this stock + politics.
    Each result: {headline, date, summary, source, keyword_matched, sentiment_hint}.
    """
    try:
        articles = get_news(ticker, days_back=days_back)
    except Exception:
        return []

    results = []
    for a in articles:
        headline = a.get("headline", "").lower()
        matched = next((kw for kw in POLITICAL_KEYWORDS if kw in headline), None)
        if matched:
            results.append({
                "headline": a.get("headline", ""),
                "date": a.get("datetime", ""),
                "summary": a.get("summary", "")[:200],
                "source": a.get("source", ""),
                "keyword_matched": matched,
                "sentiment_hint": _sentiment_hint(a.get("headline", "")),
            })
    return results


def has_recent_trump_mention(mentions: list, hours_back: int = 48) -> bool:
    """Return True if any mention in the list occurred within the last hours_back hours."""
    cutoff = datetime.utcnow() - timedelta(hours=hours_back)
    for m in mentions:
        try:
            mention_dt = datetime.strptime(m["date"], "%Y-%m-%d %H:%M")
            if mention_dt >= cutoff:
                return True
        except Exception:
            pass
    return False
