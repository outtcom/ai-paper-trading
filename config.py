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
    "Technology":             ["AAPL", "NVDA", "MSFT", "MU"],   # added MU — semis, different beta from AAPL/MSFT
    "Communication Services": ["GOOGL", "META"],
    "Consumer Discretionary": ["AMZN", "TSLA"],                 # added TSLA — growth/momentum driver
    "Healthcare":             ["LLY", "UNH"],                   # added UNH — insurance/managed care, uncorrelated to GLP-1
    "Financials":             ["JPM", "GS"],                    # added GS — higher beta, trading revenue
    "Energy":                 ["XOM", "CVX"],                   # added CVX — integrated major, slightly different exposure
    "Industrials":            ["CAT", "GE"],                    # added GE — aerospace/energy diversified
    "Consumer Staples":       ["WMT", "COST"],                  # added COST — warehouse retail, different margin profile
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
# Index ETF candidates — compete in the pipeline alongside stocks
INDEX_ETFS = ["SPY", "QQQ", "IWM", "GLD"]

# Update WATCHLIST to include ETFs
WATCHLIST = STOCKS + CRYPTO + INDEX_ETFS

# Reverse lookup: ticker → sector name
TICKER_SECTOR = {
    ticker: sector
    for sector, tickers in SECTOR_MAP.items()
    for ticker in tickers
}
# Crypto gets its own pseudo-sector
for c in CRYPTO:
    TICKER_SECTOR[c] = "Crypto"
# Index ETFs get their own pseudo-sector
for etf in INDEX_ETFS:
    TICKER_SECTOR[etf] = "Index ETF"

# ---------------------------------------------------------------------------
# VIX Regime Thresholds
# ---------------------------------------------------------------------------
VIX_LOW      = 18   # below → full sizing (1.0×)
VIX_MODERATE = 25   # below → 75% sizing
VIX_HIGH     = 35   # below → 50% sizing; above → no trades (EXTREME)

# ---------------------------------------------------------------------------
# Session Risk Controls
# ---------------------------------------------------------------------------
MAX_CONCURRENT_POSITIONS  = 5      # never hold more than 5 positions at once
MAX_PORTFOLIO_HEAT        = 0.90   # halt new entries if >90% capital deployed
MAX_NEW_POSITIONS_PER_DAY = 2      # execute up to 2 new trades/day when under-deployed
MIN_VOLUME_RATIO          = 0.8    # require recent vol ≥ 80% of 20d avg (0 = off)
BEARISH_REGIME_MULTIPLIER = 0.5    # halve size when SPY < 200d MA

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
TICKER_BETA.update({"SPY": 1.0, "QQQ": 1.2, "IWM": 1.3, "GLD": 0.1})
MAX_PORTFOLIO_BETA = 1.5     # weighted avg beta cap (crypto excluded)

# ---------------------------------------------------------------------------
# ETF Allocation Constraints
# ---------------------------------------------------------------------------
# ETF allocation constraints (strategy consultant recommendation)
ETF_MAX_ALLOCATION_PCT = 0.40   # cap combined ETF positions at 40% of total portfolio equity
ETF_OVERLAP_THRESHOLD  = 0.30   # if QQQ/IWM overlap >30% with existing Tech/SmallCap holdings, apply penalty

# ---------------------------------------------------------------------------
# VIX Rate-of-Change
# ---------------------------------------------------------------------------
VIX_ROC_THRESHOLD = 20.0    # % rise in VIX over 5 days → additional 0.5× size cut

# ---------------------------------------------------------------------------
# Model Assignments (tiered for token efficiency)
# ---------------------------------------------------------------------------
MODELS = {
    "fast":     "groq/llama-3.3-70b-versatile",  # formatters (fundamental, sentiment, technical, risk manager)
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
SESSION_DAYS = 30                  # total trading days in the session (30-day forward test)

# ---------------------------------------------------------------------------
# Telegram Notifications
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID         = os.getenv("TELEGRAM_CHAT_ID")

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
DAY_TRADE_GAP_MIN_PCT      = 2.0   # minimum pre-market gap % to flag in scanner
DAY_TRADE_VOLUME_RATIO_MIN = 1.5   # volume vs 30-day avg required for confirmation
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

# ---------------------------------------------------------------------------
# ICT FVG Scalping (paper-only, separate $5K pool, auto-close at noon)
# Strategy: Fair Value Gap + Optimal Trade Entry within NY Open Kill Zone
# ---------------------------------------------------------------------------
SCALPING_RANGE_MINUTES        = 30       # 9:30–10:00 AM opening range (unchanged)
SCALPING_DISPLACEMENT_MIN_PCT = 0.30     # min displacement % to confirm institutional bias
SCALPING_FVG_MIN_GAP_PCT      = 0.05    # min FVG size as % of price (filter noise)
SCALPING_OTE_LOW_FIBO         = 0.618   # OTE zone lower bound (Fib 61.8%)
SCALPING_OTE_HIGH_FIBO        = 0.786   # OTE zone upper bound (Fib 78.6%)
SCALPING_MIN_RRR              = 2.0     # minimum risk:reward ratio required
SCALPING_STOP_BUFFER_MULT     = 0.10    # stop = FVG boundary ± (fvg_size × 0.10)
SCALPING_VOL_RATIO_MIN        = 1.3     # volume vs baseline (unchanged)
SCALPING_CAPITAL_INIT         = 5000.0  # separate pool, never drawn from main portfolio
