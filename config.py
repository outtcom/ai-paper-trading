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
# Slippage
# ---------------------------------------------------------------------------
SLIPPAGE_PCT = 0.0015        # 15 bps on every paper fill — buys higher, sells lower

# ---------------------------------------------------------------------------
# Portfolio Beta Cap
# ---------------------------------------------------------------------------
# Approximate 2-year trailing betas vs SPY (update manually each quarter)
TICKER_BETA = {
    "AAPL": 1.2, "NVDA": 1.8, "MSFT": 1.1,
    "GOOGL": 1.2, "META": 1.3,
    "AMZN": 1.3, "LLY": 0.5,  "JPM": 1.1,
    "XOM":  0.8, "CAT": 1.2,  "WMT": 0.5,
    "FCX":  1.6, "NEE": 0.4,  "PLD": 1.0,
}
MAX_PORTFOLIO_BETA = 1.5     # weighted avg beta cap (crypto excluded)

# ---------------------------------------------------------------------------
# VIX Rate-of-Change
# ---------------------------------------------------------------------------
VIX_ROC_THRESHOLD = 20.0    # % rise in VIX over 5 days → additional 0.5× size cut

# ---------------------------------------------------------------------------
# Model Assignments (tiered for token efficiency)
# ---------------------------------------------------------------------------
MODELS = {
    "fast":     "groq/llama-3.1-70b-versatile",  # formatters (fundamental, sentiment, technical, risk manager)
    "debate":   "openai/gpt-4o-mini",             # bull/bear researchers
    "analyst":  "claude-sonnet-4-6",              # trader
    "decision": "claude-opus-4-6",                # fund manager only
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
RESEARCHER_DEBATE_ROUNDS = 1
RISK_PERSPECTIVES = ["risk_seeking", "neutral", "risk_conservative"]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
LOG_DIR   = ".tmp/logs"
STATE_DIR = ".tmp/state"

# ---------------------------------------------------------------------------
# Dashboard & Group Notifications
# ---------------------------------------------------------------------------
DASHBOARD_URL = "https://outtcom.github.io/ai-paper-trading/"

# ---------------------------------------------------------------------------
# Day Trade Signals (paper-only tracking, no capital allocated)
# ---------------------------------------------------------------------------
DAY_TRADE_GAP_MIN_PCT      = 2.0   # minimum pre-market gap % to trigger gap-and-go
DAY_TRADE_VOLUME_RATIO_MIN = 1.5   # volume vs 30-day avg required for confirmation
GAP_AND_GO_TARGET_PCT      = 1.5   # TP: target 1.5% gain same day
GAP_AND_GO_STOP_PCT        = 0.8   # SL: stop if falls 0.8% from entry
MOMENTUM_NEAR_HIGH_PCT     = 2.0   # within 2% of 52-week high qualifies
MOMENTUM_TARGET_PCT        = 2.0   # TP: target 2% gain over 1-2 days
MOMENTUM_STOP_PCT          = 1.0   # SL: stop if falls 1% from entry

# ---------------------------------------------------------------------------
# Short Selling (bear regime — SPY < 200d MA)
# ---------------------------------------------------------------------------
ALLOW_SHORT_SELLING = True
SHORT_TP_PCT        = 8.0   # target 8% decline from entry
SHORT_SL_PCT        = 4.0   # stop if price rises 4% from entry

# ---------------------------------------------------------------------------
# Sector Momentum Tilt
# ---------------------------------------------------------------------------
SECTOR_TILT_TOP_MULT    = 1.25   # amplify size for trades in top-2 sectors
SECTOR_TILT_BOTTOM_MULT = 0.75   # reduce size for trades in bottom-2 sectors
