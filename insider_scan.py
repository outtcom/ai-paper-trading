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

COMPANY_NAMES = {
    "AAPL": "Apple",           "NVDA": "Nvidia",          "MSFT": "Microsoft",
    "MU":   "Micron",          "GOOGL": "Google",         "META": "Meta",
    "AMZN": "Amazon",          "TSLA": "Tesla",            "LLY": "Eli Lilly",
    "UNH":  "UnitedHealth",    "JPM":  "JPMorgan",        "GS":  "Goldman Sachs",
    "XOM":  "ExxonMobil",      "CVX":  "Chevron",         "CAT": "Caterpillar",
    "GE":   "GE Aerospace",    "WMT":  "Walmart",         "COST": "Costco",
    "FCX":  "Freeport-McMoRan","NEE":  "NextEra Energy",  "PLD": "Prologis",
}


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


_ROMAN = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}
_SUFFIXES = {"Jr", "Jr.", "Sr", "Sr.", "Phd", "Md"}
_ENTITY_WORDS = {"TRUST", "INC", "LLC", "CORP", "LP", "FUND", "FOUNDATION", "FAMILY", "PARTNERS"}


def _clean_name(raw: str) -> str:
    """Convert 'LEVINSON ARTHUR D' → 'Arthur D. Levinson'. Entities → generic label."""
    if not raw:
        return "Company insider"
    upper_tokens = raw.upper().split()
    if any(kw in upper_tokens for kw in _ENTITY_WORDS):
        return "Company insider (institutional)"
    parts = []
    for w in raw.split():
        upper_w = w.upper()
        if upper_w in _ROMAN:          # keep roman numerals uppercase: III not Iii
            parts.append(upper_w)
        elif w.capitalize() in _SUFFIXES:
            parts.append(w.capitalize())
        else:
            parts.append(w.capitalize())
    # If last token is a single letter (middle initial), add a period
    if len(parts) > 1 and len(parts[-1]) == 1:
        parts[-1] = parts[-1] + "."
    return " ".join(parts)


def _build_alert(strong_signals: list, date: str) -> str:
    # Parse date for display
    try:
        display_date = datetime.strptime(date, "%Y-%m-%d").strftime("%B %-d, %Y")
    except Exception:
        display_date = date

    lines = [
        f"🕵️ <b>Insider Trading Report — {display_date}</b>",
        "",
        "<i>What company insiders did with their own stock this week."
        " Buys = personal conviction. Sells = usually pre-scheduled, not a warning sign.</i>",
        "",
    ]

    # Split into buys vs sells
    buy_hits  = [(t, s, m) for t, s, m in strong_signals
                 if s.get("signal_strength") in ("strong_buy", "buy")]
    sell_hits = [(t, s, m) for t, s, m in strong_signals
                 if s.get("signal_strength") in ("strong_sell", "sell")]
    buy_hits.sort( key=lambda x: x[1].get("net_open_market_value", 0), reverse=True)
    sell_hits.sort(key=lambda x: abs(x[1].get("net_open_market_value", 0)), reverse=True)

    # ── BUYS ──────────────────────────────────────────────
    if buy_hits:
        lines.append("✅ <b>INSIDER BUYS</b> — High-conviction signal")
        for ticker, sig, _ in buy_hits[:4]:
            company = COMPANY_NAMES.get(ticker, ticker)
            net_fmt = sig.get("net_open_market_fmt", "$0")
            n_buys  = sig.get("open_market_buys", 0)
            largest = sig.get("largest_single_transaction")
            who = _clean_name(largest["name"]) if largest else "An insider"
            lines.append(f"  • <b>{company} ({ticker})</b> — bought {net_fmt.lstrip('+')} on the open market")
            lines.append(f"    ↳ {who} personally purchased shares — insiders buy when they believe the price will rise.")
        lines.append("")
    else:
        lines.append("✅ <b>INSIDER BUYS</b> — None today.")
        lines.append("")

    # ── SELLS ─────────────────────────────────────────────
    if sell_hits:
        lines.append("⚠️ <b>INSIDER SELLS</b> — Usually routine, not a warning")
        for ticker, sig, _ in sell_hits[:5]:
            company  = COMPANY_NAMES.get(ticker, ticker)
            net_fmt  = sig.get("net_open_market_fmt", "$0").lstrip("+")
            n_sells  = sig.get("open_market_sells", 0)
            largest  = sig.get("largest_single_transaction")
            txn_word = "transaction" if n_sells == 1 else "transactions"
            lines.append(f"  • <b>{company} ({ticker})</b> — sold {net_fmt} across {n_sells} {txn_word}")
            if largest:
                who = _clean_name(largest["name"])
                lines.append(f"    ↳ Largest: {who} sold {largest['dollar_fmt']} on {largest['date']}")
        lines.append("  <i>Most insider sells are pre-scheduled. Only worry if the CEO starts selling large amounts with no prior pattern.</i>")
        lines.append("")

    # ── TRUMP / POLITICAL ─────────────────────────────────
    trump_hits = [
        (t, s, m) for t, s, m in strong_signals
        if has_recent_trump_mention(m, hours_back=48)
    ]
    if trump_hits:
        lines.append("🇺🇸 <b>TRUMP / POLITICAL NEWS</b>")
        seen_headlines: set = set()
        shown = 0
        for ticker, _, mentions in trump_hits:
            if shown >= 5:
                break
            recent = next((m for m in mentions if _is_within_hours(m["date"], 48)), mentions[0])
            # Deduplicate: same headline may appear across multiple tickers
            hl_key = recent["headline"][:60].lower()
            if hl_key in seen_headlines:
                continue
            seen_headlines.add(hl_key)
            company = COMPANY_NAMES.get(ticker, ticker)
            sentiment = recent["sentiment_hint"]
            if sentiment == "positive":
                emoji, label = "🟢", "Bullish"
            elif sentiment == "negative":
                emoji, label = "🔴", "Bearish"
            else:
                emoji, label = "⚪", "Watch"
            hl = recent["headline"]
            if len(hl) > 75:
                hl = hl[:72].rsplit(" ", 1)[0] + "…"
            lines.append(f"  {emoji} <b>{company} ({ticker})</b> — {label}")
            lines.append(f"    ↳ {hl}")
            shown += 1
        lines.append("")

    # ── BOTTOM LINE ───────────────────────────────────────
    buy_tickers  = [COMPANY_NAMES.get(t, t) for t, _, __ in buy_hits]
    sell_tickers = [COMPANY_NAMES.get(t, t) for t, _, __ in sell_hits]

    if buy_tickers:
        buy_str = ", ".join(buy_tickers[:3])
        lines.append(f"📌 <b>Bottom line:</b> Real insider buying at {buy_str}. Worth watching.")
    elif sell_tickers:
        lines.append("📌 <b>Bottom line:</b> No insider buying today. Widespread selling but likely all routine.")
    else:
        lines.append("📌 <b>Bottom line:</b> Quiet day — no significant insider activity.")

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
