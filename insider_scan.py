"""
Insider Activity + Political Signal Scanner
Runs at ~11:15 AM ET (Mon–Fri) via GitHub Actions.

Scans all stock tickers for:
  1. Significant SEC Form 4 insider buying/selling (60-day window)
  2. Trump/political stock mentions in news (48-hour window)

Deduplication: reads last_notified_date from docs/insider_signals.json and
only reports transactions NEWER than that date. This prevents the same
transaction showing up in every daily notification until it ages out.

Saves pre-computed signals to docs/insider_signals.json (committed to repo)
so the morning session pipeline can load them after checking out the code.
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import STOCKS
from tools.insider_tracker import compute_insider_signal
from tools.political_signal import get_trump_mentions, has_recent_trump_mention
from tools.telegram_bot import broadcast_message

SIGNALS_PATH = os.path.join("docs", "insider_signals.json")

COMPANY_NAMES = {
    "AAPL": "Apple",           "NVDA": "Nvidia",          "MSFT": "Microsoft",
    "MU":   "Micron",          "GOOGL": "Google",         "META": "Meta",
    "AMZN": "Amazon",          "TSLA": "Tesla",            "LLY": "Eli Lilly",
    "UNH":  "UnitedHealth",    "JPM":  "JPMorgan",        "GS":  "Goldman Sachs",
    "XOM":  "ExxonMobil",      "CVX":  "Chevron",         "CAT": "Caterpillar",
    "GE":   "GE Aerospace",    "WMT":  "Walmart",         "COST": "Costco",
    "FCX":  "Freeport-McMoRan","NEE":  "NextEra Energy",  "PLD": "Prologis",
}


def _load_last_notified_date() -> str:
    """Return the last date for which we sent a Telegram alert (default: 7 days ago)."""
    try:
        with open(SIGNALS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        stored = data.get("last_notified_date", "")
        if stored:
            return stored
    except Exception:
        pass
    return (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")


def _save_signals(all_signals: dict, date: str, last_notified_date: str) -> None:
    os.makedirs("docs", exist_ok=True)
    payload = {
        "date": date,
        "last_notified_date": last_notified_date,
        "signals": all_signals,
    }
    with open(SIGNALS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[insider_scan] Signals saved → {SIGNALS_PATH}")


def _format_dollar(value: float) -> str:
    abs_val = abs(value)
    if abs_val >= 1_000_000:
        return f"${abs_val / 1_000_000:.1f}M"
    if abs_val >= 1_000:
        return f"${abs_val / 1_000:.0f}K"
    return f"${abs_val:.0f}"


def _is_within_hours(date_str: str, hours: int) -> bool:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
        return dt >= datetime.utcnow() - timedelta(hours=hours)
    except Exception:
        return False


def _fmt_date(date_str: str) -> str:
    """Format 'YYYY-MM-DD' → 'Jun 7'."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%-d %b")
    except Exception:
        return date_str


def _is_10b5_1(txn: dict) -> bool:
    """Return True if Finnhub transactionCode indicates a pre-scheduled 10b5-1 sale plan."""
    code = txn.get("transaction_code", "").upper()
    return "10B5" in code


_ROMAN = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}
_SUFFIXES = {"Jr", "Jr.", "Sr", "Sr.", "Phd", "Md"}
_ENTITY_WORDS = {"TRUST", "INC", "LLC", "CORP", "LP", "FUND", "FOUNDATION", "FAMILY", "PARTNERS"}


def _clean_name(raw: str) -> str:
    """Convert 'LEVINSON ARTHUR D' → 'Arthur D. Levinson'. Entity accounts → generic label."""
    if not raw:
        return "Company insider"
    upper_tokens = raw.upper().split()
    if any(kw in upper_tokens for kw in _ENTITY_WORDS):
        return "Company insider (institutional)"
    parts = []
    for w in raw.split():
        upper_w = w.upper()
        if upper_w in _ROMAN:
            parts.append(upper_w)
        elif w.capitalize() in _SUFFIXES:
            parts.append(w.capitalize())
        else:
            parts.append(w.capitalize())
    if len(parts) > 1 and len(parts[-1]) == 1:
        parts[-1] = parts[-1] + "."
    return " ".join(parts)


def _filter_new_transactions(sig: dict, since_date: str) -> dict:
    """
    Return a copy of sig with notable_transactions and largest_single_transaction
    filtered to only include transactions after since_date. Recomputes signal_strength.
    """
    notable = [
        t for t in sig.get("notable_transactions", [])
        if t.get("date", "0000-00-00") > since_date
    ]

    # Recompute net and signal strength from new transactions only
    net = sum(
        t["dollar_value"] if t["direction"] == "buy" else -t["dollar_value"]
        for t in notable
    )
    n_buys  = sum(1 for t in notable if t["direction"] == "buy")
    n_sells = sum(1 for t in notable if t["direction"] == "sell")

    if not notable:
        return None  # nothing new to report

    largest = max(notable, key=lambda t: t["dollar_value"]) if notable else None

    # Recompute signal_strength from filtered net
    if net >= 500_000:
        strength = "strong_buy"
    elif net >= 50_000:
        strength = "buy"
    elif net <= -500_000:
        strength = "strong_sell"
    elif net <= -50_000:
        strength = "sell"
    else:
        return None  # new transactions are below threshold — skip

    new_sig = dict(sig)
    new_sig["notable_transactions"]       = notable
    new_sig["largest_single_transaction"] = largest
    new_sig["open_market_buys"]           = n_buys
    new_sig["open_market_sells"]          = n_sells
    new_sig["net_open_market_value"]      = round(net, 2)
    new_sig["net_open_market_fmt"]        = ("+" if net >= 0 else "-") + _format_dollar(net)
    new_sig["signal_strength"]            = strength
    return new_sig


def _build_alert(fresh_signals: list, date: str, since_date: str) -> str:
    """
    Build a Telegram-ready HTML message showing only fresh insider transactions.
    fresh_signals: [(ticker, filtered_sig, trump_mentions)]
    since_date: the last-notified date — shown as context in the header.
    """
    try:
        display_date = datetime.strptime(date, "%Y-%m-%d").strftime("%-d %b")
    except Exception:
        display_date = date

    try:
        since_display = datetime.strptime(since_date, "%Y-%m-%d").strftime("%-d %b")
    except Exception:
        since_display = since_date

    buy_hits  = [(t, s, m) for t, s, m in fresh_signals
                 if s.get("signal_strength") in ("strong_buy", "buy")]
    sell_hits = [(t, s, m) for t, s, m in fresh_signals
                 if s.get("signal_strength") in ("strong_sell", "sell")]
    buy_hits.sort( key=lambda x: x[1].get("net_open_market_value", 0), reverse=True)
    sell_hits.sort(key=lambda x: abs(x[1].get("net_open_market_value", 0)), reverse=True)

    lines = [
        f"🕵️ <b>Insider Activity — {display_date}</b>",
        f"<i>New transactions since {since_display}. "
        "Buys = personal conviction. Sells = usually pre-scheduled, not a warning sign.</i>",
        "",
    ]

    # ── BUYS ──────────────────────────────────────────────
    if buy_hits:
        lines.append(f"✅ <b>INSIDER BUYS</b>")
        for ticker, sig, _ in buy_hits[:4]:
            company  = COMPANY_NAMES.get(ticker, ticker)
            largest  = sig.get("largest_single_transaction")
            strength = sig.get("signal_strength", "buy")
            sentiment_tag = "🟢 <b>Strongly Bullish</b>" if strength == "strong_buy" else "🟢 <b>Bullish</b>"
            if largest:
                who       = _clean_name(largest["name"])
                amt       = largest["dollar_fmt"]
                on_date   = _fmt_date(largest["date"])
                lines.append(f"  • <b>{company} ({ticker})</b> — {sentiment_tag}")
                lines.append(f"    ↳ {who} personally bought {amt} on {on_date}")
                lines.append(f"    ↳ Insiders use their own money here — this is a conviction signal.")
            else:
                lines.append(f"  • <b>{company} ({ticker})</b> — {sentiment_tag}")
        lines.append("")
    else:
        lines.append("✅ <b>INSIDER BUYS</b> — None today.\n")

    # ── SELLS ─────────────────────────────────────────────
    if sell_hits:
        lines.append(f"⚠️ <b>INSIDER SELLS</b>")
        for ticker, sig, _ in sell_hits[:5]:
            company  = COMPANY_NAMES.get(ticker, ticker)
            largest  = sig.get("largest_single_transaction")
            strength = sig.get("signal_strength", "sell")
            is_sched = largest and _is_10b5_1(largest)
            if is_sched:
                sentiment_tag = "🟡 <b>Likely routine</b>"
            elif strength == "strong_sell":
                sentiment_tag = "🔴 <b>Bearish signal</b>"
            else:
                sentiment_tag = "🟡 <b>Probably routine</b>"

            if largest:
                who     = _clean_name(largest["name"])
                amt     = largest["dollar_fmt"]
                on_date = _fmt_date(largest["date"])
                plan_note = " (pre-scheduled 10b5-1 plan)" if is_sched else ""
                lines.append(f"  • <b>{company} ({ticker})</b> — {sentiment_tag}")
                lines.append(f"    ↳ {who} sold {amt} on {on_date}{plan_note}")
                if is_sched:
                    lines.append("    ↳ This sale was filed months in advance — not a panic sell.")
                else:
                    lines.append("    ↳ Only a concern if it's unusually large and unplanned.")
            else:
                lines.append(f"  • <b>{company} ({ticker})</b> — {sentiment_tag}")
        lines.append("")
    # no else — omit the sells section entirely if there are none (keeps message clean)

    # ── TRUMP / POLITICAL ─────────────────────────────────
    trump_hits = [
        (t, s, m) for t, s, m in fresh_signals
        if has_recent_trump_mention(m, hours_back=48)
    ]
    if trump_hits:
        lines.append("🇺🇸 <b>POLICY / POLITICAL NEWS</b>")
        seen_headlines: set = set()
        shown = 0
        for ticker, _, mentions in trump_hits:
            if shown >= 4:
                break
            recent = next((m for m in mentions if _is_within_hours(m["date"], 48)), mentions[0])
            hl_key = recent["headline"][:60].lower()
            if hl_key in seen_headlines:
                continue
            seen_headlines.add(hl_key)
            company = COMPANY_NAMES.get(ticker, ticker)
            sentiment = recent["sentiment_hint"]
            if sentiment == "positive":
                emoji, label = "🟢", "Positive"
            elif sentiment == "negative":
                emoji, label = "🔴", "Negative"
            else:
                emoji, label = "⚪", "Watch"
            hl = recent["headline"]
            if len(hl) > 80:
                hl = hl[:77].rsplit(" ", 1)[0] + "…"
            lines.append(f"  {emoji} <b>{company} ({ticker})</b> — {label}")
            lines.append(f"    ↳ {hl}")
            shown += 1
        lines.append("")

    # ── BOTTOM LINE ───────────────────────────────────────
    buy_names  = [COMPANY_NAMES.get(t, t) for t, _, __ in buy_hits]
    sell_names = [COMPANY_NAMES.get(t, t) for t, _, __ in sell_hits]
    all_scheduled = all(_is_10b5_1(s.get("largest_single_transaction") or {}) for _, s, __ in sell_hits) if sell_hits else True

    if buy_names and sell_names:
        buy_str  = ", ".join(buy_names[:2])
        sell_str = ", ".join(sell_names[:2])
        sched_note = " Sells appear pre-scheduled." if all_scheduled else ""
        lines.append(f"📌 <b>Bottom line:</b> Real insider buying at {buy_str}.{sched_note} Watch {buy_str}.")
    elif buy_names:
        buy_str = ", ".join(buy_names[:3])
        lines.append(f"📌 <b>Bottom line:</b> Insider buying at {buy_str}. This is a positive signal worth watching.")
    elif sell_names:
        sched_note = " All appear pre-scheduled (10b5-1)." if all_scheduled else " No clear conviction signal."
        lines.append(f"📌 <b>Bottom line:</b> Sells at {', '.join(sell_names[:3])}.{sched_note} No action required.")
    elif trump_hits:
        lines.append("📌 <b>Bottom line:</b> No insider buying or selling. Watch for policy impact on affected stocks.")
    else:
        lines.append("📌 <b>Bottom line:</b> No new significant insider activity today.")

    return "\n".join(lines)


def main():
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    print(f"[insider_scan] ========== Insider Scan {today} ==========")

    # Load last notified date for deduplication
    last_notified_date = _load_last_notified_date()
    print(f"[insider_scan] Reporting only transactions since {last_notified_date}")
    print(f"[insider_scan] Scanning {len(STOCKS)} stocks...")

    all_signals   = {}
    fresh_signals = []   # (ticker, filtered_sig, trump_mentions) — only new transactions

    for ticker in STOCKS:
        try:
            print(f"[insider_scan] Scanning {ticker}...", end=" ", flush=True)
            insider_signal = compute_insider_signal(ticker, days_back=60)
            trump_mentions = get_trump_mentions(ticker, days_back=7)

            all_signals[ticker] = {
                "insider_signal": insider_signal,
                "trump_mentions": trump_mentions,
                "scanned_at":    datetime.utcnow().isoformat(),
            }

            # Filter to only transactions newer than last_notified_date
            filtered_sig = _filter_new_transactions(insider_signal, last_notified_date)
            has_trump    = has_recent_trump_mention(trump_mentions, hours_back=48)

            if filtered_sig or has_trump:
                sig_to_report = filtered_sig or insider_signal  # use full sig for trump-only entries
                fresh_signals.append((ticker, sig_to_report, trump_mentions))
                strength_label = (filtered_sig or {}).get("signal_strength", "trump_only")
                trump_label = " | Political news" if has_trump else ""
                print(f"{strength_label}{trump_label} (NEW)")
            else:
                print("no new activity")

        except Exception as e:
            print(f"ERROR: {e}")
            all_signals[ticker] = {"error": str(e), "scanned_at": datetime.utcnow().isoformat()}

        time.sleep(0.5)  # stay under Finnhub 60 calls/min rate limit

    # Save signals to disk (always — pipeline agents load this regardless of alerts)
    _save_signals(all_signals, today, last_notified_date)
    print(f"[insider_scan] Found {len(fresh_signals)} tickers with new activity.")

    if fresh_signals:
        alert = _build_alert(fresh_signals, today, last_notified_date)
        try:
            broadcast_message(alert)
            # Update last_notified_date so we don't repeat these transactions tomorrow
            _save_signals(all_signals, today, today)
            print(f"[insider_scan] Alert sent. last_notified_date updated to {today}.")
        except Exception as e:
            print(f"[insider_scan] Telegram alert failed (signals still saved): {e}")
    else:
        print("[insider_scan] No new insider activity since last run — no alert sent.")
        # Still bump last_notified_date so the window doesn't grow indefinitely
        _save_signals(all_signals, today, today)


if __name__ == "__main__":
    main()
