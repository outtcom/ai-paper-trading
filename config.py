"""
Central configuration for the trading system.
Edit this file to change tickers, risk settings, and model assignments.
"""
import os

# ---------------------------------------------------------------------------
# Sector-organised watchlist — one flagship per GICS sector
# Add/remove tickers here; the rest of the system adapts automatically.
# ---------------------------------------------------------------------------

SECTOR_MAP = {
    "Technology":             ["AAPL", "NVDA", "MSFT"],
    "Communication Services": ["GOOGL", "META"],
    "Consumer Discretionary": ["AMZN"],
    "Healthcare":             ["LLY"],        # Eli Lilly — GLP-1 leader
    "Financials":             ["JPM"],        # JPMorgan — largest US bank
    "Energy":                 ["XOM"],        # ExxonMobil — energy bellwether
    "Industrials":            ["CAT"],        # Caterpillar — economic cycle indicator
    "Consumer Staples":       ["WMT"],        # Walmart — defensive anchor
    "Materials":              ["FCX"],        # Freeport-McMoRan — copper/gold play
    "Utilities":              ["NEE"],        # NextEra — clean energy leader
    "Real Estate":            ["PLD"],        # Prologis — e-commerce logistics REIT
}

# Sector ETFs used for strength/momentum analysis (SPDR suite)
SECTOR_ETFS = {
    "Technology":             "XLK",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Healthcare":             "XLV",
    "Financials":             "XLF",
    "Energy":                 "XLE",
    "Industrials":            "XLI",
    "Consumer Staples":       "XLP",
    "Materials":              "XLB",
    "Utilities":              "XLU",
    "Real Estate":            "XLRE",
}

CRYPTO   = ["BTC-USD", "ETH-USD", "SOL-USD"]
STOCKS   = [ticker for tickers in SECTOR_MAP.values() for ticker in tickers]
WATCHLIST = STOCKS + CRYPTO

# Reverse lookup: ticker → sector name
TICKER_SECTOR = {
    ticker: sector
    for sector, tickers in SECTOR_MAP.items()
    for ticker in tickers
}
# Crypto gets its own pseudo-sector
for c in CRYPTO:
    TICKER_SECTOR[c] = "Crypto"

# ---------------------------------------------------------------------------
# VIX Regime Thresholds
# ---------------------------------------------------------------------------
VIX_LOW      = 18   # below → full sizing (1.0×)
VIX_MODERATE = 25   # below → 75% sizing
VIX_HIGH     = 35   # below → 50% sizing; above → no trades (EXTREME)

# ---------------------------------------------------------------------------
# Session Risk Controls
# ---------------------------------------------------------------------------
MAX_CONCURRENT_POSITIONS = 2      # never hold more than 2 positions at once
MAX_PORTFOLIO_HEAT       = 0.75   # halt new entries if >75% capital deployed
MIN_VOLUME_RATIO         = 0.8    # require recent vol ≥ 80% of 20d avg (0 = off)
BEARISH_REGIME_MULTIPLIER = 0.5   # halve size when SPY < 200d MA

# ---------------------------------------------------------------------------
# Model Assignments (tiered for token efficiency)
# ---------------------------------------------------------------------------
MODELS = {
    "fast":     "claude-haiku-4-5-20251001",   # data retrieval, summarisation
    "analyst":  "claude-sonnet-4-6",            # analysts, researcher debate, trader
    "decision": "claude-opus-4-6",              # risk management, fund manager only
}

# ---------------------------------------------------------------------------
# Trading Parameters
# ---------------------------------------------------------------------------
INITIAL_CAPITAL      = 5_000           # paper trading starting capital (USD)
MAX_POSITION_SIZE    = 0.25            # max 25% of portfolio in any single name
DEFAULT_RISK_PROFILE = "moderate"      # aggressive | moderate | conservative

# ---------------------------------------------------------------------------
# Session Settings
# ---------------------------------------------------------------------------
SESSION_DAYS = 10                  # total trading days in the session

# ---------------------------------------------------------------------------
# Telegram Notifications
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID         = os.getenv("TELEGRAM_CHAT_ID")
APPROVAL_TIMEOUT_SECONDS = 3600    # 1 hour to approve/reject a trade

# ---------------------------------------------------------------------------
# Data Settings
# ---------------------------------------------------------------------------
NEWS_LOOKBACK_DAYS         = 7     # days of news to pull per analysis cycle
REDDIT_POST_LIMIT          = 25    # number of Reddit posts to fetch per ticker
TECHNICAL_INDICATOR_PERIOD = 60    # trading days of price history for indicators

# ---------------------------------------------------------------------------
# Researcher Debate & Risk Perspectives
# ---------------------------------------------------------------------------
RESEARCHER_DEBATE_ROUNDS = 2
RISK_PERSPECTIVES = ["risk_seeking", "neutral", "risk_conservative"]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
LOG_DIR   = ".tmp/logs"
STATE_DIR = ".tmp/state"
