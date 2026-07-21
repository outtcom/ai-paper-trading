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
# Beta-Scaled Minimum Stop Loss
# ---------------------------------------------------------------------------
# Minimum stop loss = ticker_beta × BETA_MIN_STOP_FACTOR.
# Prevents tight stops on high-beta names (e.g. NVDA beta=1.8 → 7.2% min stop).
# Set to 0.0 to disable.
BETA_MIN_STOP_FACTOR = 0.04

# ---------------------------------------------------------------------------
# Portfolio Beta Cap
# ---------------------------------------------------------------------------
# Approximate 2-year trailing betas vs SPY (update manually each quarter)
TICKER_BETA = {
    "AAPL": 1.2, "NVDA": 1.8, "MSFT": 1.1, "MU":   1.4,
    "GOOGL": 1.2, "META": 1.3,
    "AMZN": 1.3, "TSLA": 1.9,
    "LLY":  0.5, "UNH":  0.6,
    "JPM":  1.1, "GS":   1.3,
    "XOM":  0.8, "CVX":  0.8,
    "CAT":  1.2, "GE":   1.2,
    "WMT":  0.5, "COST": 0.7,
    "FCX":  1.6, "NEE":  0.4, "PLD": 1.0,
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
SESSION_DAYS   = 90                # total trading days in the session (90-day forward test)
PROMPT_VERSION = "1.0.0"          # bump when any agent prompt changes — isolates IC data per prompt era

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
# Conviction Floor Override
# "" lets the strategy consultant control the floor (recommended).
# Set to "medium" or "high" to override for debugging.
# ---------------------------------------------------------------------------
CONVICTION_FLOOR_OVERRIDE = ""

# ---------------------------------------------------------------------------
# Mid-Cap Universe — S&P 400 representative names, 2+ per GICS sector
# Price range ~$15–$250, market cap $2B–$10B
# ---------------------------------------------------------------------------
MIDCAP_UNIVERSE = [
    # Technology
    "MANH", "EPAM", "PAYC", "FTNT", "WEX", "FFIV", "AKAM", "SAIC",
    # Communication Services
    "CABO", "LBRDK", "NWSA", "IPG",
    # Consumer Discretionary
    "POOL", "LKQ", "SIG", "GNTX", "BWA", "FOXF", "LEVI",
    # Consumer Staples
    "INGR", "POST", "SFM", "CHEF", "USFD",
    # Healthcare
    "PODD", "NTRA", "HOLX", "DVA", "HSIC", "PRGO", "PDCO",
    # Financials
    "CINF", "AIZ", "FHN", "SNV", "HWC", "WTFC", "ESNT",
    # Industrials
    "XPO", "RBC", "GNRC", "ITT", "GATX", "AIT", "AAON",
    # Materials
    "AXTA", "EMN", "FMC", "OLN", "TROX",
    # Energy
    "OVV", "SM", "CIVI", "RRC", "AR", "CNX",
    # Utilities
    "OGE", "NWE", "SR", "OTTR",
    # Real Estate
    "NNN", "OHI", "EPR", "STAG", "ROIC",
]

# Mid-Cap Signal Parameters
MIDCAP_TARGET_PCT       = 5.0      # TP: 5% gain
MIDCAP_STOP_PCT         = 3.0      # SL: 3% loss
MIDCAP_HOLD_DAYS        = 5        # auto-close after 5 trading days
MIDCAP_MIN_VOLUME_RATIO = 1.5      # 3d avg vol / 20d avg vol
MIDCAP_MIN_RSI          = 45       # not in downtrend
MIDCAP_MAX_RSI          = 78       # not overbought (relaxed from 72 — strong breakouts read 73–77)
MIDCAP_CAPITAL_INIT     = 5000.0

# ---------------------------------------------------------------------------
# Penny Stock Universe — curated liquid sub-$10 names, price-filtered at scan time
# Dynamic price filter applied at runtime: only $1.00–$10.00 tickers qualify
# ---------------------------------------------------------------------------
PENNY_UNIVERSE = [
    "SNDL", "PLUG", "FCEL", "BLNK", "CLSK", "MVIS", "CLOV",
    "AGEN", "BTBT", "HIMS", "NKLA", "SPWR", "RUN", "BETR",
    "NNDM", "WKHS", "IDEX", "MMAT", "TRCH", "AMPE",
    "CTRM", "GNUS", "EXPR", "BFIN", "EDTK", "TAOP",
    "AEVA", "CGRN", "VVPR", "APGN", "BSFC", "PAVS",
    "LKCO", "HPNN", "AEYE", "EDBL", "SIDU", "PNRG",
    "GREE", "DFLI", "BMTM", "BFRI", "CYCC", "BRTX",
    "GOVX", "LQDA", "ORIC", "ELME", "ARDS", "LRMR",
]

# Penny Stock Signal Parameters
PENNY_MIN_PRICE         = 1.00     # filter below $1 (sub-penny trap)
PENNY_MAX_PRICE         = 10.00    # filter above $10 (no longer penny)
PENNY_MIN_AVG_DAILY_VOL = 500_000  # liquidity floor (30d avg shares/day)
PENNY_VOLUME_SPIKE_RATIO = 3.0     # 3d avg vol / 30d avg vol
PENNY_TARGET_PCT        = 8.0      # TP: 8% gain
PENNY_STOP_PCT          = 5.0      # SL: 5% loss
PENNY_HOLD_DAYS         = 2        # auto-close after 2 trading days
PENNY_CAPITAL_INIT      = 5000.0
PENNY_MAX_POSITION_PCT  = 0.20     # max 20% of pool per signal — gap risk mitigation
PENNY_MAX_CONCURRENT    = 5        # up to 5 signals at 20% each
