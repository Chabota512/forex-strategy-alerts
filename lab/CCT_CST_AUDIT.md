# CCT/CST deterministic audit report

## Run and scope

- Regression command: `python3 -m unittest lab.tests.test_engine.CstTests.test_cct_cst_audit_fixtures_match_every_candle`
- Result: **passed**. All 34 expected CCT states and 34 expected CST states across the four fixtures match the engine output.
- Fixture configuration: `swing_neighbors = 1`, `break_basis = "close"`. State pairs below are indexed from candle 0 and written `expected / actual`.
- The frozen v1.5.0 strategy spec was not changed. No CCT/CST engine-versus-spec bug was found or fixed in this audit.

## Per-candle fixture results

Candles are shown as `(open, high, low, close)`.

### Clean higher-high / higher-low uptrend

Candles: `0 (9,10,8,9); 1 (11,12,7,11); 2 (10,11,9,10); 3 (12,13,8,12); 4 (11,12,10,11); 5 (13,14,9,13); 6 (14.5,15,11,14.5)`.

- CCT: `0 ranging/ranging; 1 ranging/ranging; 2 ranging/ranging; 3 ranging/ranging; 4 uptrend/uptrend; 5 uptrend/uptrend; 6 uptrend/uptrend`.
- CST: `0 undefined/undefined; 1 undefined/undefined; 2 undefined/undefined; 3 undefined/undefined; 4 undefined/undefined; 5 undefined/undefined; 6 uptrend/uptrend`.

### Clean lower-high / lower-low downtrend

Candles: `0 (11,12,10,11); 1 (9,13,8,9); 2 (10,11,9,10); 3 (8,12,7,8); 4 (9,10,8,9); 5 (7,11,6,7); 6 (5.5,9,5,5.5)`.

- CCT: `0 ranging/ranging; 1 ranging/ranging; 2 ranging/ranging; 3 ranging/ranging; 4 downtrend/downtrend; 5 downtrend/downtrend; 6 downtrend/downtrend`.
- CST: `0 undefined/undefined; 1 undefined/undefined; 2 undefined/undefined; 3 undefined/undefined; 4 undefined/undefined; 5 undefined/undefined; 6 downtrend/downtrend`.

### Mixed swings / ranging

Candles: `0 (9,10,8,9); 1 (10,12,7,10); 2 (10,11,9,10); 3 (8,13,6,8); 4 (10,12,8,10); 5 (7,14,5,7); 6 (8,13,7,8)`.

- CCT: `0 ranging/ranging; 1 ranging/ranging; 2 ranging/ranging; 3 ranging/ranging; 4 ranging/ranging; 5 ranging/ranging; 6 ranging/ranging`.
- CST: `0 undefined/undefined; 1 undefined/undefined; 2 undefined/undefined; 3 undefined/undefined; 4 undefined/undefined; 5 undefined/undefined; 6 undefined/undefined`.

### Close-confirmed CHoCH and reversal

Candles: `0 (9,10,8,9); 1 (11,12,7,11); 2 (10,11,9,10); 3 (12,13,8,12); 4 (11,12,10,11); 5 (13,14,9,13); 6 (14.5,15,11,14.5); 7 (12,14.5,10.5,12); 8 (15.5,16,11,15.5); 9 (9.5,12,9,9.5); 10 (8,10,7.5,8); 11 (9,11,8.5,9); 12 (7,10,6.8,7)`.

- CCT: `0 ranging/ranging; 1 ranging/ranging; 2 ranging/ranging; 3 ranging/ranging; 4 uptrend/uptrend; 5 uptrend/uptrend; 6 uptrend/uptrend; 7 uptrend/uptrend; 8 uptrend/uptrend; 9 uptrend/uptrend; 10 uptrend/uptrend; 11 ranging/ranging; 12 downtrend/downtrend`.
- CST: `0 undefined/undefined; 1 undefined/undefined; 2 undefined/undefined; 3 undefined/undefined; 4 undefined/undefined; 5 undefined/undefined; 6 uptrend/uptrend; 7 uptrend/uptrend; 8 uptrend/uptrend; 9 transition/transition; 10 transition/transition; 11 transition/transition; 12 downtrend/downtrend`.

## Engine-versus-spec findings

Every expected state in the fixture file matched the corresponding engine state; there were **no divergences** and therefore no engine/spec regression fix was needed. The regression is pinned by `CstTests.test_cct_cst_audit_fixtures_match_every_candle`, which computes CCT at every prefix and compares every CST state.

The compact fixtures set one shared `swing_neighbors = 1` for both indicators. The frozen spec describes two neighbors for CCT and five for CST. These fixtures therefore verify the state transitions under their declared compact test setting, not the production swing sizes. Do not treat this test-only parameter as an amendment to the strategy spec; expanding the fixtures to exercise the production parameters remains an owner decision.

## Outputs that may look wrong to a human — owner decisions

These are synthetic-fixture observations, not claims about a labeled market chart. The state outputs match the current spec. Any desired change to labels or transition timing requires the owner's explicit spec decision.

1. **The early clean uptrend stays neutral-looking.** In the uptrend fixture, candles `0 (9,10,8,9); 1 (11,12,7,11); 2 (10,11,9,10); 3 (12,13,8,12)` produce `CCT=ranging` and `CST=undefined` at every index. The frozen spec says CCT requires at least two swing highs and two swing lows or returns “ranging / unclear”; CST starts undefined until a confirmed level breaks. A human may call the visible sequence bullish before those confirmations. At candle 4 CCT becomes `uptrend`; CST stays `undefined` through candle 5 and becomes `uptrend` at candle 6.

2. **The early clean downtrend has the mirrored delay.** In the downtrend fixture, candles `0 (11,12,10,11); 1 (9,13,8,9); 2 (10,11,9,10); 3 (8,12,7,8)` produce `CCT=ranging` and `CST=undefined`. The same two-swing CCT requirement and undefined-until-break CST rule apply. CCT becomes `downtrend` at candle 4; CST becomes `downtrend` at candle 6. A human may identify the bearish sequence earlier.

3. **A range is named differently by the two indicators.** In the mixed fixture, candles `0 (9,10,8,9); 1 (10,12,7,10); 2 (10,11,9,10); 3 (8,13,6,8); 4 (10,12,8,10); 5 (7,14,5,7); 6 (8,13,7,8)` yield `CCT=ranging` and `CST=undefined` for all seven candles. CCT explicitly maps mixed swings to ranging/unclear, while CST's frozen state list uses `undefined` when structural trend has not been established. A user may expect CST to say “ranging” too; that wording/state distinction is an owner decision.

4. **A visually forceful reversal remains in transition.** In the CHoCH fixture, candle `9 (9.5,12,9,9.5)` breaks the prior uptrend's confirmed low on a close. Candles `10 (8,10,7.5,8)` and `11 (9,11,8.5,9)` keep CST at `transition`; CCT changes from `uptrend` to `ranging` at candle 11. CST becomes `downtrend` only at candle `12 (7,10,6.8,7)`. The spec says a post-CHoCH swing must confirm the new trend, while a break of the old trend's saved level resumes the old trend. A human may call the continued fall bearish before the structural confirmation arrives. The required confirmation delay and transition label are owner decisions, not an engine/spec bug.

No owner-labeled chart screenshot was supplied for a separate visual comparison.
