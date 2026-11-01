# Candlestick Trigger — Strategy Specification

**Version:** 1.5
**Date:** 2026-10-27
**Status:** Confirmed via labeled examples (owner-reviewed). Not yet backtested.
**Scope:** First test — candlestick trigger ONLY. No trend, FVG, session, swing, or higher-timeframe filters. Journal context is deferred by design.

---

## 1. Confirmed Rule (v1.0)

Compare each **fully closed** candle (`new`) with the candle immediately **immediately before** it (`prev`). Evaluate once, on candle close.

**SELL alert — ALL must hold:**
1. `new.high > prev.high` (strict; a touch is not enough)
2. `new.close < new.open` (new candle is red/bearish)
3. `prev.close > prev.open` (prev candle is green/bullish — the opposite color)

**BUY alert — ALL must hold:**
1. `new.low < prev.low` (strict; a touch is not enough)
2. `new.close > new.open` (new candle is green/bullish)
3. `prev.close < prev.open` (prev candle is red/bearish — the opposite color)

**Otherwise: NO ALERT.**

### Formulas

```
SELL  ⟺  (new.high > prev.high) ∧ (new.close < new.open) ∧ (prev.close > prev.open)
BUY   ⟺  (new.low  < prev.low ) ∧ (new.close > new.open) ∧ (prev.close < prev.open)
```

### Pseudocode

```
on_candle_close(prev, new):
    prev_bull = prev.close > prev.open
    prev_bear = prev.close < prev.open
    new_bull  = new.close  > new.open
    new_bear  = new.close  < new.open

    if new_bear and prev_bull and new.high > prev.high:
        emit("SELL pattern")
    elif new_bull and prev_bear and new.low < prev.low:
        emit("BUY pattern")
    else:
        no alert

    # optional, non-blocking note (see §3):
    if abs(new.close - new.open) < abs(prev.close - prev.open):
        attach_note("smaller body / weaker follow-through")
```

### Consequences already fixed by strictness
- **Equal highs/lows:** `new.high == prev.high` or `new.low == prev.low` → NO ALERT (comparison is strict).
- **Perfect doji as `new`:** `new.close == new.open` → fails both directional-close conditions → NO ALERT.
- **Doji as `new` with a directional close** (tiny body, open ≠ close): treated like any candle of that color. Confirmed by labeled example (image(4).png: doji with close < open after a green candle → SELL).
- **Mutual exclusivity:** a candle cannot be both a BUY and a SELL, since it cannot close both above and below its own open.
- **One-side-only logic:** a SELL never looks at lows; a BUY never looks at highs. A candle can break both prev extremes; only the new candle's close direction decides which side (if any) fires.

---

## 2. Scenario / Decision Table

| # | Prev | New | High break (strict) | Low break (strict) | Alert | Why |
|---|------|-----|--------------------|--------------------|-------|-----|
| 1 | green | red | YES | — | **SELL** | all 3 sell conditions |
| 2 | green | red | NO | — | no alert | no strict high break |
| 3 | green | red | touch only | — | no alert | touch ≠ break |
| 4 | green | red | equal | — | no alert | strict inequality fails |
| 5 | red | red | YES | — | no alert | prev not opposite color |
| 6 | doji | red | YES | — | OPEN QUESTION (see §5) | prev color undefined |
| 7 | red | green | — | YES | **BUY** | all 3 buy conditions |
| 8 | red | green | — | NO | no alert | no strict low break |
| 9 | red | green | — | touch only | no alert | touch ≠ break |
| 10 | red | green | — | equal | no alert | strict inequality fails |
| 11 | green | green | — | YES | no alert | prev not opposite color |
| 12 | doji | green | — | YES | OPEN QUESTION (see §5) | prev color undefined |
| 13 | any | doji (open==close) | either | either | no alert | fails directional close |
| 14 | green | red | YES | YES | **SELL** | sell only needs high break |
| 15 | red | green | YES | YES | **BUY** | buy only needs low break |
| 16 | red | green | YES | NO | no alert | buy needs low break, not high |
| 17 | green | red | NO | YES | no alert | sell needs high break, not low |
| 18 | any | any | NO | NO | no alert | no break at all |

---

## 3.5 CurrentCandleTrend (CCT) — descriptive context (v1.1, window defined in v1.2)

**Definition (working, confirmed by owner on example image(8).png):** trend read from the recent visible candle sequence using the last two swing highs and swing lows:

- **Uptrend:** higher swing high + higher swing low
- **Downtrend:** lower swing high + lower swing low
- **Ranging / unclear:** mixed swings, or too few swings to classify

**Window (v1.2, owner decision):** CCT is computed over the **last N closed candles, default N = 15**, where N is a **user-configurable setting** in the website (adjustable per timeframe if desired). Swing = a candle whose high (or low) is the extreme of its 2 neighbors on each side (fractal-style). Classification requires at least 2 swing highs and 2 swing lows within the window; otherwise → ranging/unclear.

**Status in v1.2:** CCT is recorded with every alert as *context only* (v1.4: CST took over the confirmation role; CCT's role going forward is owner-decided — see open questions). It must NOT create, cancel, or modify alerts.

**Alert output format (multi-candle charts):**
1. What I see
2. Current trend (CCT)
3. Alert (per §1 rule — unchanged)
4. Reason

First confirmed example: image(8).png — CCT = **downtrend**, alert = **BUY** (buy into a downtrend is allowed; CCT is descriptive only).

---

## 3.6 Current Swing Trend (CST) — structural context (v1.3, owner selected structure-based method)

**Concept:** CST is the *structural* trend — the big HH/HL or LH/LL picture — as opposed to CCT (§3.5), which is the fast 15-candle read. CST changes only when market structure breaks: **BOS** (break of structure, continuation) or **CHoCH** (change of character, first warning of reversal). Language aligns with owner's journal.

**Mechanics (working definition — parameters marked ◊ are assumptions pending owner confirmation):**

1. **Major swings:** fractal extremes with **5 candles on each side** ◊ (vs. 2 for CCT's minor swings). Track the most recent **major swing high** and **major swing low**.
2. **Breaks are evaluated on candle CLOSE** ◊ (closed candles only — consistent with the alert rule).
3. **States:** `uptrend` / `downtrend` / `undefined` (not enough structure yet).

**State machine (per closed candle):**

- **Uptrend:**
  - `close > last_major_high` → **BOS up** — uptrend continues; new major highs update as they form.
  - `close < last_major_low` (the most recent higher low) → **CHoCH** — uptrend in question; state → `transition`.
- **Transition (after a CHoCH against the old trend):**
  - break beyond the most recent swing of the *old* trend's side → new trend confirmed via **BOS** in the new direction.
  - failure and a close back beyond the old trend's level → old trend resumes.
- **Downtrend:** mirror of uptrend.
- **Undefined:** the first close beyond either most recent major high or low sets the initial trend.

**Status (v1.4 update — owner decision):** CST is the **confirmation layer** for the main wick-break strategy. Every alert carries a strength label based on CST alignment (proposal below). CST labels strength only — they do NOT create alerts, and in v1.4 they do NOT cancel alerts either (owner decides; see open questions). Labels are qualitative, never probabilities.

**Proposed strength labels (working, pending owner confirmation):**

| Alert | CST state | Proposed label |
|---|---|---|
| SELL | downtrend | **STRONG** — with structure |
| SELL | ranging/undefined | **NEUTRAL** — unconfirmed |
| SELL | uptrend | **WEAK** — against structure |
| BUY | uptrend | **STRONG** — with structure |
| BUY | ranging/undefined | **NEUTRAL** — unconfirmed |
| BUY | downtrend | **WEAK** — against structure |

If CST is in `transition` (post-CHoCH, unconfirmed), label = NEUTRAL until a BOS confirms the new structure.

**Worked examples (owner's charts):**
- **image(11):** sharp low → CHoCH up → repeated closes above major highs (BOS) → **CST uptrend** all session. CCT agreed most of the time.
- **image(12):** top forms, then closes below major lows (BOS down); mid-chart bounce never closed above a major high → **CST downtrend** throughout.
- **image(9):** the vertical spike broke minor structure but — on a closing basis ◊ — did not break the major swing high → **CST stays downtrend** while CCT flipped repeatedly. (If the spike *did* close above a major high, CST would have flipped up at that point — labeling this chart would confirm the close-vs-wick question.)
- **image(13):** persistent LH/LL → **CST downtrend**, agrees with CCT.
- **image(10):** overlapping swings, no close beyond major levels → **CST undefined/ranging** while CCT flipped constantly.

---

## 3. Secondary Observation — NOT an alert condition

- Body size = `abs(close − open)` (bodies only, never high-to-low range).
- If `new` body < `prev` body → optional note: *"smaller body / weaker follow-through."*
- This note must NEVER create, cancel, block, or override an alert. It is not a confidence score.
- No threshold or exact comparison method defined yet.

---

## 4. Labeled Examples Log

| Example | Prev | New | Label | Note |
|---------|------|-----|-------|------|
| image.png | green | red (breaks prev high) | SELL ✓ | confirmed by owner |
| image(1).png | red | green (breaks prev low) | BUY ✓ | confirmed by owner |
| image(2).png | red | green (no low break) | NO ALERT | owner correction — wick did NOT break prev low; visual reading was wrong |
| image(3).png | green | red (no high break, wick at/below prev high) | NO ALERT | confirmed by silence |
| image(4).png | green | doji, close < open, breaks prev high | SELL ✓ | owner confirmed; doji with directional close counts |
| image(5).png | red | green (low marginally below prev low) | BUY ✓ | tiny margin — needs OHLC confirmation in data (pixel judgment unreliable) |
| image(6).png | green | red (breaks prev high by wide margin) | SELL ✓ | confirmed by silence |
| image(7).png | green (middle of 3) | red (breaks prev high) | SELL ✓ | confirmed by silence |

**Key labeling lesson (from image(2).png):** wick breaks must be judged from exact OHLC numbers, never from visual wick length in a rendered chart. The live/ backtest implementation compares numeric `high`/`low` values only.

---

## 5. Open Questions (unresolved — do not guess)

1. **Prev is a doji** (`prev.close == prev.open`): prev has no defined color. Does that block alerts, or should only "strictly defined" colors count for prev? (Scenarios 6 and 12.)
2. **Both-breaks candle** (scenarios 14–15): default reading is "close direction decides." Confirm.
3. **Body-size threshold:** any quantitative version of the §3 note is undefined.
4. **Earlier journal example** ("large red followed by much smaller green"): buy/no-alert label still unconfirmed.
4. **CST-as-confirmation rules:** label names confirmed (STRONG/NEUTRAL/WEAK); WEAK alerts still fire (confirmed v1.5); `transition` = NEUTRAL.
5. **CST parameters:** break basis **close — CONFIRMED by owner v1.5**; major-swing fractal size 5 (working); transition state confirmed.
8. **Opposite-alert cooldown (NEW v1.5):** when opposite-direction alerts fire close together (e.g., SELL then BUY within a few candles), which is preferred? Owner direction: *"the stronger / more confirmed alert is preferred"* — to be decided FROM BACKTEST DATA, not by guessing. Implementation: log opposite-alert clusters during backtest (pair each opposite alert within N candles of a prior alert, record both alerts' strength labels and follow-through), then decide the suppression rule from evidence.
6. **CCT method details:** fixed N-candle window (default 15, configurable) confirmed; adaptive scan-back (until 2 valid swings found, capped ~25) left as a possible future setting. Swing definition (fractal, 2 neighbors each side) is the working method — owner may correct.
7. **Data provider:** free, no credit card, must support GBPUSD + USDJPY + XAUUSD, streaming or pollable OHLC for M1/M5/M15/H1/H4, and permit website use. Not yet verified against current official docs — must recheck before recommendation.

---

## 6. Hard Constraints (from owner)

- Alerts are **informational only**. Owner decides whether to trade.
- **No** automatic trading, order placement, take-profit, or stop-loss. Ever.
- Do not claim probability of success or profitability without properly tested data.
- No MT5 dependency.
- Forex prices vary between providers; no universal price, no zero-delay promises.
- Primary test input: structured OHLC candle data. Screenshots are example records only — not the live detector.
- Journal concepts (weak break of major swing high, BOS/CHoCH, premium retracement, 15-min FVG, NY 8:30 level) are **deferred context filters** — to be added later as separate, versioned modules if the owner chooses.

---

## 7. Change Log

- **v1.5 (2026-10-27):** Owner confirmations: CST break basis = close; WEAK alerts still fire; opposite-alert cooldown deferred to data-driven decision (cluster logging spec'd); earlier unlabeled 'large red → smaller green' example resolved = NO ALERT (matches image(2).png correction).
- **v1.4 (2026-10-27):** Owner decision: wick-break rule = main strategy; CST = confirmation layer labeling alert strength (with/neutral/against structure proposed). Labels are qualitative, never probabilities; they create no alerts and cancel none in v1.4.
- **v1.3 (2026-10-27):** Added CST (Current Swing Trend) — structure-based method selected by owner (option c): anchored to most recent major swing high/low, updated on BOS/CHoCH breaks. Parameters (5-candle fractal, close-based breaks, transition state) marked as working assumptions. Context-only.
- **v1.2 (2026-10-27):** CCT window defined: last N closed candles, default N=15, user-configurable setting. Swing detection method specified (fractal extremes, 2 neighbors per side, min 2 swings per side, else ranging). CCT remains context-only.
- **v1.1 (2026-10-27):** Added CurrentCandleTrend (CCT) as descriptive context alongside alerts; confirmed on image(8).png (downtrend + BUY). Alert rule unchanged.
- **v1.0 (2026-10-27):** Initial spec. Confirmed: strict wick-break trigger, directional close on `new`, opposite-color requirement on `prev`, doji handling, no-alert defaults. Direction-of-prev rule confirmed by owner ("YES IT MATTERS") and validated on examples.
