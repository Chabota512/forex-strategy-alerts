---
name: Replay settings parity
description: Keep batch and streaming replay snapshot metadata comparable.
---

Batch and streaming replay parity must use the same normalized simulation settings for both simulation and snapshot serialization.

**Why:** Replay snapshots carry settings-derived capital context; omitting settings on one path creates false divergence even when alerts and trades match.

**How to apply:** When building parity tests or production snapshot adapters, pass the identical `SimulationSettings` payload to the batch simulation and every snapshot append.
