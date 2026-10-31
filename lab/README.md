# Forex Strategy Lab

A local, file-based research tool for candle strategies. Batch backtests,
simulator-based evolution, versioned JSON specs, and the replay page do not
place orders or fetch market prices.

## Run the lab

```sh
python -m unittest discover -s lab/tests -v
python lab/generate_synthetic_data.py
python lab/backtest.py
```

The generator creates repeatable weekday-only synthetic M5/M15 files for GBPUSD,
USDJPY, and XAUUSD across 12 calendar-month buckets. Synthetic prices are
software fixtures, not historical market data or trading evidence. The default
seed is 140; use `--end-date YYYY-MM-DD` to reproduce a different calendar
window.

The backtest writes `lab/output/alerts.csv.gz` and `lab/output/report.json`. Each
alert row records its spec version, approval status, settings fingerprint, full
CST and reward settings, input source/hash, and run ID. The JSON report stores
the complete effective settings and per-file provenance, including STRONG,
NEUTRAL, and WEAK validation reward and alert counts per symbol/timeframe and
overall. If any effective setting changes, the fingerprint and run ID change.

## Simulator-based evolution

Run one owner-present round from a terminal:

```sh
python -m lab.evolve --rounds 1
```

The loop requires the selected local M1 file to have an owner-approved,
hash-matched sanity report before it will score real data. See
`lab/data/README.md` and the sanity-check section below. Proposals use the
`LLM_PROVIDER` environment variable (`auto` by default, or `gemini` or `groq`).
In `auto` mode, the request tries `GEMINI_API_KEY`, then
`GEMINI_API_KEY_2`, then `GROQ_API_KEY`, then `GROQ_API_KEY_2`. It falls back
only for authentication, quota, rate-limit, or network failures before a model response; malformed
proposals are archived and discredited without fallback. Archives record the
provider, key index, usage, and sanitized failure details, never key material.
The model receives the champion's training-period simulator metrics only;
validation results stay inside the local evaluator. A candidate is eligible
only when it meets the validation trade and calendar-month floors, improves
total validation net P&L after costs by at least 10%, and has positive
expectancy. Promotion requires an explicit terminal `y` response. The frozen
seed file is never edited.

The optimizer supports XAUUSD M5/M15 using January–September 2025 for training
and October–December 2025 for validation. Its objective is total simulated
net P&L after the simulator's per-instrument costs. The old batch reward scorer
below remains available for its existing reports but is not used by the
evolution loop.

## Export and replay

Export any version to readable text:

```sh
python lab/export.py --spec lab/specs/seed_v1.5.0.json \
  --output lab/output/seed_v1.5.0.txt
```

Open `lab/replay.html` in a browser, then select one candle CSV and the
precomputed `lab/output/alerts.csv.gz`. The page decompresses and streams the
local files in the browser, supports instrument/timeframe selection, alert
markers, stepping, playback, and a speed control. It has no network calls and
does not run the strategy.

## Local candle files

Place normalized files in `lab/data/` using names such as `GBPUSD_M1.csv`.
Required columns are:

```text
time,open,high,low,close
```

Timestamps must be ISO-8601; offset-free input timestamps are treated as UTC.
Rows must be strictly chronological, with valid positive OHLC prices. A
consistent optional `source` column can identify the source, for example
`dukascopy` or `synthetic`. The public loader
`load_candles(symbol, timeframe)` returns records with UTC ISO-8601 time strings
and validated `Decimal` OHLC values. It reads local files only and does not
depend on Dukascopy or any other price API.

The loader supports M1 files, but the backtest remains M5/M15. Run
`python lab/resample.py lab/data/GBPUSD_M1.csv` to write complete UTC M5/M15
bars to the ignored `lab/output/resampled/` directory. Incomplete intervals
are omitted and reported. Synthetic sample files, local market CSVs, sanity
reports, and generated run outputs are ignored by Git and deployment packaging.

## Real-data sanity check

Before scoring non-synthetic candles, compare a local CSV with a second local
reference CSV and review the resulting report:

```sh
python lab/data_sanity.py \
  --symbol GBPUSD \
  --timeframe M1 \
  --primary lab/data/GBPUSD_M1.csv \
  --reference path/to/reference_gbpusd_m1.csv
```

The reference needs a `time`/`datetime`/`date` column with an explicit UTC
offset and a `close` column. The checker compares exact UTC timestamps; it does
not fetch data, resample, choose an acceptable price tolerance, or approve
results automatically. It reports mismatched timestamps and sampled close
differences. The report starts as `PENDING_OWNER_REVIEW`; real-data backtest
scoring remains blocked until the owner reviews and approves it. If checking
H1, provide same-timeframe H1 inputs; do not derive H1 from M1.

## Legacy batch scoring and safety

- Score exactly the next 12 candles after each alert candle in the same
  timeframe; no alert-candle or later-than-window prices are used.
- Use the configured per-symbol D_min/A_max distances and the approved
  target/adverse, tie, speed, and follow-through rules.
- The legacy batch scorer objective is total validation-period reward. Its
  12-month window uses months 1–9 for training and 10–12 for validation.
- Validation floors are at least 100 alerts per symbol/timeframe and alerts in
  at least 8 of the 12 months overall. Floors are never relaxed.
- Reports also split validation reward and alert counts by STRONG, NEUTRAL, and
  WEAK, per symbol/timeframe and overall. This is descriptive reporting only:
  strength labels do not affect alert generation or the reward objective.
- Alerts with fewer than 12 future candles are marked
  `INSUFFICIENT_LOOKAHEAD`; they do not receive a score.
- The seed remains frozen at version 1.5.0. A promoted champion is separately
  archived and remains owner-approved and frozen.
- All alerts are informational analysis only. No orders, auto-trading, or
  profitability/probability claims are supported.
