"""
Central configuration for the trading system.
Edit this file to change tickers, risk settings, and model assignments.
"""

# --- Watchlist ---
STOCKS   = ["AAPL", "GOOGL", "NVDA", "MSFT", "AMZN"]
CRYPTO   = ["BTC-USD", "ETH-USD", "SOL-USD"]
WATCHLIST = STOCKS + CRYPTO

# --- VIX Regime Thresholds ---
VIX_LOW      = 18   # below → full sizing (1.0x)
VIX_MODERATE = 25   # below → 75% sizing
VIX_HIGH     = 35   # below → 50% sizing; above → no trades

# --- Model Assignments (tiered for token efficiency) ---
MODELS = {
    "fast": "claude-haiku-4-5-20251001",      # data retrieval, summarization
    "analyst": "claude-sonnet-4-6",            # analyst reports, researcher debate
    "decision": "claude-opus-4-6",             # risk management, fund manager only
}

# --- Trading Parameters ---
INITIAL_CAPITAL = 5_000            # paper trading starting capital (USD)
MAX_POSITION_SIZE = 0.10           # max 10% of portfolio in any single stock
DEFAULT_RISK_PROFILE = "moderate"  # aggressive | moderate | conservative

# --- Session Settings ---
SESSION_DAYS = 10                  # total trading days in the session

# --- Telegram Notifications ---
import os
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
APPROVAL_TIMEOUT_SECONDS = 3600   # 1 hour to approve/reject a trade

# --- Data Settings ---
NEWS_LOOKBACK_DAYS = 7             # days of news to pull per analysis cycle
REDDIT_POST_LIMIT = 25             # number of Reddit posts to fetch per ticker
TECHNICAL_INDICATOR_PERIOD = 60    # trading days of price history for indicators

# --- Researcher Debate Rounds ---
RESEARCHER_DEBATE_ROUNDS = 2

# --- Risk Manager Perspectives ---
RISK_PERSPECTIVES = ["risk_seeking", "neutral", "risk_conservative"]

# --- Paths ---
LOG_DIR = ".tmp/logs"
STATE_DIR = ".tmp/state"
