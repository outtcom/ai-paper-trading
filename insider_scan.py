"""
Insider Activity + Political Signal Scanner
Runs at 7:30 AM ET (Mon–Fri) via GitHub Actions, before the morning session.

Scans all stock tickers (not crypto/ETFs) for:
  1. Significant SEC Form 4 insider buying/selling (60-day window)
  2. Trump/political stock mentions in news (7-day window)

Saves pre-computed signals to docs/insider_signals.json (committed to repo)
so the morning session pipeline can load them after checking out the code.

Sends a Telegram alert when significant activity is detected.
"""
import json
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import STOCKS
from tools.insider_tracker import compute_insider_signal
from tools.political_signal import get_trump_mentions, has_recent_trump_mention
from tools.telegram_bot import broadcast_message


def _save_signals(all_signals: dict, date: str) -> None:
    os.makedirs("docs", exist_ok=True)
    path = os.path.join("docs", "insider_signals.json")
    payload = {"date": date, "signals": all_signals}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[insider_scan] Signals saved → {path}")


def _format_dollar(value: float) -> str:
    abs_val = abs(value)
    if abs_val >= 1_000_000:
        return f"${abs_val / 1_000_000:.1f}M"
    if abs_val >= 1_000:
        return f"${abs_val / 1_000:.0f}K"
    return f"${abs_val:.0f}"


def _is_within_hours(date_str: str, hours: int) -> bool:
    from datetime import timedelta
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
        return dt >= datetime.utcnow() - timedelta(hours=hours)
    except Exception:
        return False


def _build_alert(strong_signals: list, date: str) -> str:
    lines = [f"🕵️ <b>INSIDER SCAN — {date}</b>\n"]

    insider_hits = [
        (ticker, sig, _) for ticker, sig, _ in strong_signals
        if sig.get("signal_strength") in ("strong_buy", "strong_sell", "buy", "sell")
    ]
    insider_hits.sort(key=lambda x: abs(x[1].get("net_open_market_value", 0)), reverse=True)

    if insider_hits:
        lines.append("<b>Open-Market Insider Activity:</b>")
        for ticker, sig, _ in insider_hits[:5]:
            label = sig["signal_strength"].upper().replace("_", " ")
            net_fmt = sig.get("net_open_market_fmt", "$0")
            buys  = sig.get("open_market_buys", 0)
            sells = sig.get("open_market_sells", 0)
            lines.append(f"  • <b>{ticker}</b> — {label}  ({buys} buy / {sells} sell, net {net_fmt})")
            largest = sig.get("largest_single_transaction")
            if largest:
                name = largest.get("name", "Insider")
                dv   = largest.get("dollar_fmt", _format_dollar(largest.get("dollar_value", 0)))
                lines.append(f"    ↳ {name}: {largest['direction']} {dv} on {largest['date']}")
        lines.append("")

    trump_hits = [
        (ticker, sig, mentions) for ticker, sig, mentions in strong_signals
        if has_recent_trump_mention(mentions, hours_back=48)
    ]
    if trump_hits:
        lines.append("<b>Trump/Political Headlines (last 48h):</b>")
        for ticker, _, mentions in trump_hits[:5]:
            recent = next(
                (m for m in mentions if _is_within_hours(m["date"], 48)), mentions[0]
            )
            emoji = "📈" if recent["sentiment_hint"] == "positive" else ("📉" if recent["sentiment_hint"] == "negative" else "⚠️")
            headline = recent["headline"][:90]
            lines.append(f"  {emoji} <b>{ticker}</b> — {headline}")
        lines.append("")

    lines.append(f"<i>{len(STOCKS)} stocks scanned. Signals loaded for morning pipeline.</i>")
    return "\n".join(lines)


def main():
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    print(f"[insider_scan] ========== Insider Scan {today} ==========")
    print(f"[insider_scan] Scanning {len(STOCKS)} stocks...")

    all_signals = {}
    strong_signals = []

    for ticker in STOCKS:
        try:
            print(f"[insider_scan] Scanning {ticker}...", end=" ", flush=True)
            insider_signal = compute_insider_signal(ticker, days_back=60)
            trump_mentions = get_trump_mentions(ticker, days_back=7)

            all_signals[ticker] = {
                "insider_signal": insider_signal,
                "trump_mentions": trump_mentions,
                "scanned_at": datetime.utcnow().isoformat(),
            }

            is_significant = insider_signal.get("signal_strength") in ("strong_buy", "strong_sell", "buy", "sell")
            has_trump = has_recent_trump_mention(trump_mentions, hours_back=48)

            strength_label = insider_signal["signal_strength"]
            trump_label = " | Trump mention" if has_trump else ""
            print(f"{strength_label}{trump_label}")

            if is_significant or has_trump:
                strong_signals.append((ticker, insider_signal, trump_mentions))

        except Exception as e:
            print(f"ERROR: {e}")
            all_signals[ticker] = {"error": str(e), "scanned_at": datetime.utcnow().isoformat()}

        time.sleep(0.5)  # stay well under 60 API calls/min

    _save_signals(all_signals, today)
    print(f"[insider_scan] Found {len(strong_signals)} significant signals.")

    if strong_signals:
        alert = _build_alert(strong_signals, today)
        try:
            broadcast_message(alert)
            print("[insider_scan] Alert sent.")
        except Exception as e:
            print(f"[insider_scan] Telegram alert failed (signals still saved): {e}")
    else:
        print("[insider_scan] No significant signals today — no alert sent.")


if __name__ == "__main__":
    main()
