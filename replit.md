# Forex Strategy Lab

A local, file-based strategy research station: candle-trigger alerts, causal
market-structure detection, risk/reward simulation, and a guarded AI evolution
loop — over real historical data for five pairs (XAUUSD, GBPUSD, USDJPY,
GBPJPY, AUDJPY).

## Data (source of truth)

- `lab/data/historicalMarketData/&lt;SYMBOL&gt;/` — raw Dukascopy M1 year-files.
  Canonical raw source data; preserve it for reproducible rebuilds.
- `lab/data/&lt;SYMBOL&gt;/&lt;TF&gt;/&lt;YYYY-MM&gt;.csv` — monthly files per timeframe
  (M1, M5, M15, H1, H4, D1). Gold's verified monthly dataset and manifest are
  tracked and are the app's canonical read path; fresh clones need no regeneration.
  `python -m lab.resample --all` remains an intentional rebuild utility.
- Data gate: scoring a symbol requires an OWNER_APPROVED manifest in
  `lab/data/sanity_checks/` whose SHA-256 matches the source file. Gold's
  known-good M1 hash: `bf6b9ab77db1a6f103b8fc2a7236a0bcb01fef35a628271903e3b2df4b35ddd0`.
- AUDJPY spans 2024–2025; the other pairs 2023–2025. All UTC.

## Run & operate

- `python -m unittest discover -s lab/tests -v` — full strategy + system suite
- `python -m lab.evolve --rounds 1` — run one guarded evolution round (terminal; owner approves promotions)
- `python -m lab.resample --all` — regenerate all derived monthly files from raws
- `python lab/populate_snapshots.py` — rebuild alert snapshots + digest from a full run
- `python -m lab.query --help` — query snapshots by feature combination
- App: start the managed Replit workflows (Python API on :8080 + control-room UI)
- `pnpm run typecheck` / `pnpm run build` — workspace checks
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API clients from OpenAPI

## Where things live

- `lab/specs/seed_v1.5.0.json` — frozen strategy definition (never edit)
- `lab/specs/champion.json` — current promoted strategy (frozen)
- `lab/specs/policy_current.json` — notification policy (passthrough until a promotion)
- `lab/specs/proposals/` — every evolve round, archived (proposal + result)
- `lab/` — engine (trigger, CCT, CST, shadow detectors), simulator (incl.
  `candle_geometry` owner exit model), evolve loop, alert_policy, snapshots,
  digest, query, llm_client (Gemini×2 → Groq fallback), server.py
- `artifacts/forex-control-room/` — the replay/simulation/evolve UI
- `lab/UPDATED_BUILD_PLAN.txt` — the owner-approved build plan (amendments §27+)

## Architecture decisions

- **Spec-as-data:** the strategy is a JSON document executed by a deterministic
  engine; the AI (and owner) change specs, never code paths.
- **Causality everywhere:** every indicator, swing, label, and connector is
  confirmed-candle-only; prefix-invariance tests enforce it.
- **Expectancy objective:** promotion requires positive validation expectancy
  (R/trade) after costs on a 12-month out-of-sample window — never P&L totals.
- **Figures-only AI feeding:** models receive compact digests, never raw rows.
- **Immutable raw events, versioned policy:** the AI FILTER on the alert tape
  only removes alerts when a promoted policy says so; archives keep everything.

## Product & safety

Alerts and simulations are informational only — no orders, no auto-trading,
no profitability claims. The owner makes every trading decision and every
strategy promotion personally.

## Gotchas

- The seed is FROZEN (owner-approved). Never edit `seed_v1.5.0.json` or
  `champion.json`; promotions happen through the evolve loop's terminal gate.
- NEVER force-push. History rewrites have cost us the audit trail repeatedly.
- NEVER delete canonical data before its hash-verified replacement exists.
- Git LFS is forbidden for market data — `lab/tests/test_lfs_tripwire.py`
  fails if any tracked data file becomes an LFS pointer.
- Plain `git push origin main` only; push after every batch.
- Track all verified gold monthly timeframe files and their manifest; do not
  ignore them or convert them to Git LFS. The app reads those files directly.
- Raw Dukascopy archives remain tracked and unchanged as rebuild sources.

## User preferences

- Keep the system minimal, local, and file-based.
- Every AI-visible metric is a validated figure, never a raw row, never a claim.
- One focused agent batch per feature; ONE final report; no narration.