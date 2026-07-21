from typing import Dict, List
"""
Fetch news, financial statements, and insider transactions from Finnhub.
Free tier: 60 API calls/minute.
"""
import os
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()


def _client():
    import finnhub
    return finnhub.Client(api_key=os.environ["FINNHUB_API_KEY"])


def _fh_call(func, *args, max_retries: int = 3, **kwargs):
    """Call a Finnhub API function with exponential backoff on rate-limit errors."""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            err = str(e).lower()
            if "429" in err or "rate limit" in err or "too many" in err:
                wait = 2 ** attempt  # 1s, 2s, 4s
                print(f"[finnhub] Rate limit hit — retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
            else:
                raise
    return None


def get_news(ticker: str, days_back: int = 7) -> List[Dict]:
    """
    Fetch recent news articles for a ticker.
    Returns list of {datetime, headline, summary, source, url}.
    """
    client = _client()
    end = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    articles = _fh_call(client.company_news, ticker, _from=start, to=end) or []
    results = []
    for a in articles[:20]:  # cap at 20 to stay within token budget
        results.append({
            "datetime": datetime.fromtimestamp(a["datetime"]).strftime("%Y-%m-%d %H:%M"),
            "headline": a.get("headline", ""),
            "summary": a.get("summary", "")[:500],
            "source": a.get("source", ""),
            "url": a.get("url", ""),
        })
    return results


def get_financials(ticker: str) -> dict:
    """
    Fetch basic financial metrics: P/E, EPS, revenue growth, debt/equity.
    Returns dict of key metrics.
    """
    client = _client()
    metrics = _fh_call(client.company_basic_financials, ticker, "all") or {}
    metric = metrics.get("metric", {})
    return {
        "pe_ratio": metric.get("peNormalizedAnnual"),
        "eps_ttm": metric.get("epsTTM"),
        "revenue_growth_3y": metric.get("revenueGrowth3Y"),
        "gross_margin": metric.get("grossMarginTTM"),
        "debt_equity": metric.get("totalDebt/totalEquityAnnual"),
        "roe": metric.get("roeTTM"),
        "current_ratio": metric.get("currentRatioAnnual"),
        "52w_high": metric.get("52WeekHigh"),
        "52w_low": metric.get("52WeekLow"),
    }


def get_insider_transactions(ticker: str) -> List[Dict]:
    """
    Fetch recent insider buy/sell transactions.
    Returns list of {name, share, change, transaction_date, transaction_price}.
    """
    client = _client()
    data = _fh_call(client.stock_insider_transactions, ticker) or {}
    transactions = data.get("data", [])[:10]
    results = []
    for t in transactions:
        results.append({
            "name": t.get("name", ""),
            "shares": t.get("share", 0),
            "change": t.get("change", 0),
            "date": t.get("transactionDate", ""),
            "price": t.get("transactionPrice", 0),
        })
    return results


def get_company_profile(ticker: str) -> dict:
    """Fetch company profile: name, industry, market cap, description."""
    client = _client()
    profile = _fh_call(client.company_profile2, symbol=ticker) or {}
    return {
        "name": profile.get("name", ""),
        "industry": profile.get("finnhubIndustry", ""),
        "market_cap": profile.get("marketCapitalization", 0),
        "country": profile.get("country", ""),
        "exchange": profile.get("exchange", ""),
        "ipo_date": profile.get("ipo", ""),
        "website": profile.get("weburl", ""),
    }


def get_news_sentiment(ticker: str) -> dict:
    """
    Fetch Finnhub's ML-derived news sentiment scores.
    Returns buzz + bullish/bearish percentages. Free tier, works from any IP.
    Used as fallback when Reddit public JSON API is blocked (cloud IPs).
    """
    client = _client()
    try:
        data = _fh_call(client.news_sentiment, ticker) or {}
        sentiment = data.get("sentiment", {})
        buzz = data.get("buzz", {})
        return {
            "ticker": ticker,
            "bullish_pct": round(sentiment.get("bullishPercent", 0) * 100, 1),
            "bearish_pct": round(sentiment.get("bearishPercent", 0) * 100, 1),
            "articles_last_week": buzz.get("articlesInLastWeek", 0),
            "buzz_score": round(buzz.get("buzz", 0), 3),
            "company_news_score": round(data.get("companyNewsScore", 0), 3),
            "sector_avg_bullish_pct": round(data.get("sectorAverageBullishPercent", 0) * 100, 1),
        }
    except Exception as e:
        return {
            "ticker": ticker,
            "bullish_pct": 50,
            "bearish_pct": 50,
            "articles_last_week": 0,
            "buzz_score": 0,
            "company_news_score": 0,
            "sector_avg_bullish_pct": 50,
            "error": str(e),
        }


if __name__ == "__main__":
    ticker = "AAPL"
    print("News:", get_news(ticker, days_back=3))
    print("Financials:", get_financials(ticker))
    print("Insiders:", get_insider_transactions(ticker))
    print("Profile:", get_company_profile(ticker))
    print("News Sentiment:", get_news_sentiment(ticker))
