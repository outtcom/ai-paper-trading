# Operator Protocol — Session 3

**Written: 2026-06-25 (Day 1/90)**
**Operator: Fahad Syed**

This document defines your role, daily/weekly routine, and the rules you commit to *before* the session begins. Rules written after losses are rationalisations. These are not.

---

## Your Role

You are the **system operator**, not a trader. Your job is to:

- Keep the infrastructure running (secrets, billing, GHA runners)
- Monitor agent accuracy and system health
- Make deliberate, evidence-based parameter changes
- Stay out of the way when the system is working

You do **not** pick stocks, override trades, or second-guess individual agent decisions. The 8-agent pipeline does that. Your edge is in system design and discipline — not in reacting to daily P&L.

---

## Daily Routine (Mon–Fri)

| Time (ET) | Action |
|---|---|
| ~11 AM | Read pre-flight Telegram report. GREEN = nothing to do. YELLOW = note it. RED = investigate. |
| ~6:30 PM | Read EOD recap. Note equity, alpha, and any closed trades. |
| Anytime | If circuit breaker fires → read the alert, investigate root cause, do NOT reset it for 48 hours. |

**Total daily time: ~5 minutes.** If you're spending more than 15 minutes/day reacting to the system, something is wrong with the system — not with your attention.

---

## Weekly Routine

| When | Action |
|---|---|
| Sunday 6 PM ET | Read weekly briefing (auto-sent to Telegram). |
| Sunday only | Make any config or prompt changes for the following week. |
| Sunday only | Review agent IC stats (visible in Telegram after every 5th closed trade). |

**Parameter changes are Sunday-only.** No mid-week edits to `config.py`, agent prompts, or risk controls — regardless of how a week went. The one exception is a genuine bug fix (code that prevents the system from running at all).

---

## Locked Parameters (Session 3)

These are set at session start and cannot change until Session 4:

| Parameter | Value | Locked |
|---|---|---|
| Initial capital | $5,000 | ✅ |
| Session length | 90 trading days | ✅ |
| Circuit breaker drawdown | 15% | ✅ |
| Fund Manager model | `claude-opus-4-6` | ✅ |
| Paper trading only | `paper-api.alpaca.markets` | ✅ |

If you feel pressure to change any of these mid-session, write down why in a note and revisit on Sunday. If the reason still feels valid after 48 hours, it probably is. If it doesn't, it was a reaction.

---

## The 48-Hour Rule

**After any of these events, wait 48 hours before making changes:**

1. Three consecutive losing trades
2. A RED health report
3. A week where alpha goes negative by more than 5%
4. Any emotional reaction to a single day's P&L

During the 48-hour window: observe only. You may investigate root cause, write notes, read logs. You may not edit code, change config, or add/remove tickers.

**Why 48 hours?** Loss aversion causes over-adjustment. Changes made within 48h of a bad outcome are statistically worse than no change at all — you're optimising for yesterday's loss, not tomorrow's edge.

---

## What You CAN Change Mid-Session

These are allowed at any time (with documentation):

| Change | Condition |
|---|---|
| Fix a crash/bug | Code error preventing the workflow from running |
| Update watchlist | New ticker justified by fundamental thesis, not recent price action |
| Change `MAX_CONCURRENT_POSITIONS` | Only after 10+ days of data showing capacity constraint |
| Bump `PROMPT_VERSION` | After changing an agent prompt — let it run 5 days before evaluating IC |
| Add a GHA secret | API key rotation or new provider |

---

## What You CANNOT Change Mid-Session

| Change | Why |
|---|---|
| Downgrade Fund Manager below Opus | Non-negotiable quality gate |
| Raise `INITIAL_CAPITAL` | Adds capital to cover losses — distorts session results |
| Remove the circuit breaker | Removes the only hard stop on catastrophic loss |
| Switch to live trading | Paper session only — no exceptions |
| Change session length mid-session | Extends a losing session or cuts a winning one short |
| Override a Fund Manager HOLD manually | You are not the Fund Manager |

---

## Pause Protocol

If you need to step away (travel, life, bad mental state):

```bash
python pause_session.py --reason "travelling until 2026-07-05"
```

This disables all cron triggers by adding `if: false` guards to the workflow files. Existing positions are held. No new trades execute. Resume with:

```bash
python resume_session.py
```

Pause is not a loss-avoidance tool. If you pause because you're down and want to "wait for better conditions," that is market timing and it will cost you. Pause only for operational reasons (you can't monitor for >5 days, API billing needs attention, genuine system failure).

---

## When the Session Ends

Session 3 ends at Day 90 (≈ 2026-11-26 ET). At that point:

1. Read the session summary auto-sent to Telegram
2. Write a post-mortem: What worked? What didn't? What should change for Session 4?
3. Wait at least 72 hours before starting Session 4 (no hot restarts after a bad session)
4. Archive the post-mortem in `docs/`

---

## Accountability Check

Before making any mid-session change, answer these three questions:

1. **Is this fixing a bug or chasing a result?** Bug = proceed. Chasing = wait 48h.
2. **Would I make this change if the last 5 trades were all winners?** If no, don't make it.
3. **Can I justify this in the post-mortem?** If you'd be embarrassed writing it down, don't do it.

---

*This document is the governing protocol for Session 3. It takes precedence over in-the-moment judgement.*
