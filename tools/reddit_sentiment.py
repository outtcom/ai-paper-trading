"""
Reddit sentiment tool — uses Reddit's public JSON API (no credentials required).
Append .json to any Reddit URL to get structured data.

Rate limit: ~10 requests/minute. No API key, no approval process needed.
Reddit removed self-service API keys in Nov 2025; public JSON endpoints remain available.
"""
import re
import time
import requests
from typing import Dict, List

SUBREDDITS = ["wallstreetbets", "stocks", "investing"]
POSITIVE_WORDS = {"buy", "bull", "bullish", "long", "calls", "moon", "rally", "upside", "strong", "growth", "breakout", "surge"}
NEGATIVE_WORDS = {"sell", "bear", "bearish", "short", "puts", "crash", "drop", "downside", "weak", "overvalued", "dump", "collapse"}

HEADERS = {
    "User-Agent": "trading-system/1.0 (sentiment research, non-commercial)"
}


def get_posts(ticker: str, limit: int = 25) -> List[Dict]:
    """
    Search recent Reddit posts mentioning the ticker using public JSON API.
    Returns list of {title, score, num_comments, created, sentiment_score, subreddit}.
    """
    results = []
    posts_per_sub = max(1, limit // len(SUBREDDITS))

    for subreddit in SUBREDDITS:
        try:
            url = f"https://www.reddit.com/r/{subreddit}/search.json"
            params = {
                "q": ticker,
                "sort": "new",
                "t": "week",
                "limit": posts_per_sub,
                "restrict_sr": "true",
            }
            response = requests.get(url, headers=HEADERS, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            for post in data.get("data", {}).get("children", []):
                p = post.get("data", {})
                text = (p.get("title", "") + " " + p.get("selftext", "")).lower()
                sentiment = _score_sentiment(text)
                results.append({
                    "subreddit": subreddit,
                    "title": p.get("title", "")[:200],
                    "score": p.get("score", 0),
                    "num_comments": p.get("num_comments", 0),
                    "created": p.get("created_utc", 0),
                    "sentiment_score": sentiment,
                })

            time.sleep(0.5)  # be respectful of rate limits (~10 req/min)

        except requests.exceptions.RequestException as e:
            print(f"[reddit_sentiment] Failed to fetch r/{subreddit}: {e}")
            continue

    return results


def get_sentiment_summary(ticker: str, limit: int = 25) -> Dict:
    """
    Return aggregated sentiment summary for use by the sentiment analyst agent.
    """
    posts = get_posts(ticker, limit)

    if not posts:
        return {
            "ticker": ticker,
            "post_count": 0,
            "avg_sentiment": 0.0,
            "sentiment_label": "neutral",
            "note": "No Reddit posts found or API unavailable",
            "top_posts": [],
        }

    scores = [p["sentiment_score"] for p in posts]
    avg = sum(scores) / len(scores)
    label = "bullish" if avg > 0.1 else "bearish" if avg < -0.1 else "neutral"

    return {
        "ticker": ticker,
        "post_count": len(posts),
        "avg_sentiment": round(avg, 3),
        "sentiment_label": label,
        "top_posts": sorted(posts, key=lambda x: x["score"], reverse=True)[:5],
    }


def _score_sentiment(text: str) -> float:
    """Simple lexicon-based sentiment: +1 per positive word, -1 per negative word, normalized."""
    words = set(re.findall(r"\b\w+\b", text))
    pos = len(words & POSITIVE_WORDS)
    neg = len(words & NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / total


if __name__ == "__main__":
    summary = get_sentiment_summary("AAPL", limit=10)
    import json
    print(json.dumps(summary, indent=2))
