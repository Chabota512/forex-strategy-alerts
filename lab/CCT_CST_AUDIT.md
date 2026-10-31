# CCT/CST deterministic audit

The fixture suite asserts each candle's CCT and CST state for clean HH/HL,
clean LH/LL, mixed/ranging swings, and a close-confirmed CHoCH sequence.

## Owner decision

The frozen v1.5.0 rules remain authoritative when a visual chart reading differs:
CCT requires confirmed swing pairs; CST uses the frozen close-based break and
waits for confirmed pivots. Therefore CCT may report `ranging` while CST remains
`undefined`, and CST may stay in `transition` until a post-CHoCH swing breaks.
These states are intentional, not grounds to alter the frozen strategy spec.
No owner-labeled chart screenshot was supplied for a separate visual comparison.

The audit changes no strategy specification. Review the fixture state arrays
before changing any behavior that would disagree with the frozen rules.
