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


def get_news(ticker: str, days_back: int = 7) -> List[Dict]:
    """
    Fetch recent news articles for a ticker.
    Returns list of {datetime, headline, summary, source, url}.
    """
    client = _client()
    end = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    articles = client.company_news(ticker, _from=start, to=end)
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
    metrics = client.company_basic_financials(ticker, "all")
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
    data = client.stock_insider_transactions(ticker)
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
    profile = client.company_profile2(symbol=ticker)
    return {
        "name": profile.get("name", ""),
        "industry": profile.get("finnhubIndustry", ""),
        "market_cap": profile.get("marketCapitalization", 0),
        "country": profile.get("country", ""),
        "exchange": profile.get("exchange", ""),
        "ipo_date": profile.get("ipo", ""),
        "website": profile.get("weburl", ""),
    }


if __name__ == "__main__":
    ticker = "AAPL"
    print("News:", get_news(ticker, days_back=3))
    print("Financials:", get_financials(ticker))
    print("Insiders:", get_insider_transactions(ticker))
    print("Profile:", get_company_profile(ticker))
