from typing import Dict, List
"""
Compute technical analysis indicators from OHLCV data using pandas-ta.
Returns a structured dict of indicator values for use by the technical analyst agent.
"""
import pandas as pd
import numpy as np


def compute_indicators(bars: List[Dict]) -> dict:
    """
    Given a list of OHLCV bar dicts, compute key technical indicators.
    Returns dict of indicator name -> latest value (or dict of values).
    """
    if len(bars) < 20:
        return {"error": "Insufficient data — need at least 20 bars"}

    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})

    # pandas-ta not used — all indicators computed manually below

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    def safe_last(series):
        val = series.dropna()
        return round(float(val.iloc[-1]), 4) if len(val) > 0 else None

    # Trend
    ema_20 = close.ewm(span=20).mean()
    ema_50 = close.ewm(span=50).mean()
    sma_20 = close.rolling(20).mean()

    # Momentum
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_14 = 100 - (100 / (1 + rs))

    # MACD
    ema_12 = close.ewm(span=12).mean()
    ema_26 = close.ewm(span=26).mean()
    macd_line = ema_12 - ema_26
    macd_signal = macd_line.ewm(span=9).mean()
    macd_hist = macd_line - macd_signal

    # Bollinger Bands
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    # Volume
    vol_sma_20 = volume.rolling(20).mean()

    # ATR
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr_14 = tr.rolling(14).mean()

    current_close = safe_last(close)

    return {
        "current_close": current_close,
        "trend": {
            "ema_20": safe_last(ema_20),
            "ema_50": safe_last(ema_50),
            "sma_20": safe_last(sma_20),
            "price_vs_ema20": round((current_close / safe_last(ema_20) - 1) * 100, 2) if current_close and safe_last(ema_20) else None,
            "price_vs_ema50": round((current_close / safe_last(ema_50) - 1) * 100, 2) if current_close and safe_last(ema_50) else None,
        },
        "momentum": {
            "rsi_14": safe_last(rsi_14),
            "rsi_signal": "overbought" if safe_last(rsi_14) and safe_last(rsi_14) > 70 else "oversold" if safe_last(rsi_14) and safe_last(rsi_14) < 30 else "neutral",
        },
        "macd": {
            "macd_line": safe_last(macd_line),
            "signal_line": safe_last(macd_signal),
            "histogram": safe_last(macd_hist),
            "crossover": "bullish" if safe_last(macd_hist) and safe_last(macd_hist) > 0 else "bearish",
        },
        "bollinger_bands": {
            "upper": safe_last(bb_upper),
            "middle": safe_last(bb_mid),
            "lower": safe_last(bb_lower),
            "bandwidth": round((safe_last(bb_upper) - safe_last(bb_lower)) / safe_last(bb_mid) * 100, 2) if safe_last(bb_mid) else None,
            "price_position": "near_upper" if current_close and safe_last(bb_upper) and current_close > safe_last(bb_mid) else "near_lower",
        },
        "volume": {
            "current_volume": int(volume.iloc[-1]),
            "avg_volume_20d": int(safe_last(vol_sma_20)) if safe_last(vol_sma_20) else None,
            "volume_ratio": round(float(volume.iloc[-1]) / float(vol_sma_20.iloc[-1]), 2) if vol_sma_20.iloc[-1] else None,
        },
        "volatility": {
            "atr_14": safe_last(atr_14),
            "atr_pct": round(safe_last(atr_14) / current_close * 100, 2) if current_close and safe_last(atr_14) else None,
        },
    }


if __name__ == "__main__":
    from tools.alpaca_data import get_ohlcv
    bars = get_ohlcv("AAPL", "2023-11-01", "2024-01-10")
    indicators = compute_indicators(bars)
    import json
    print(json.dumps(indicators, indent=2))
