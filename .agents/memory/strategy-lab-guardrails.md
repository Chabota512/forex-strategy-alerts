---
name: Strategy Lab guardrails
description: Project-specific constraints for strategy files, data integrity, validation, and Git publishing.
---

**Rule:** Keep `lab/specs/seed_v1.5.0.json` and `lab/specs/champion.json` byte-for-byte frozen. Their expected SHA-256 hashes are `dc4b73c09cc16eceae907263d84f8543991b364c2817ff66189f845183425fd4` and `bdf1fb83a5046083c6d3397d49d96473f2692b2d49c20e9a26fbfaba7158df4e`.

**Why:** The project instructions designate these as immutable baselines.

**How to apply:** Verify both hashes before and after related work; do not edit either file.

**Rule:** Keep all verified XAUUSD monthly CSVs and the monthly manifest tracked as the app's canonical read dataset. Preserve the raw Dukascopy archives as rebuild sources; never ignore or LFS-track the monthly files.

**Why:** The user directed that fresh clones must include the gold monthly dataset and run without regenerating it.

**How to apply:** Read monthly files directly in app consumers. Treat `python -m lab.resample --all` as an intentional rebuild utility, not startup setup.

**Rule:** Keep validation deterministic and offline; do not make live LLM calls. Treat raw market data as canonical and never overwrite or remove it until a replacement has been verified. Never force-push; only push to an existing `origin` with a plain `git push origin main`.

**Why:** The project instructions prioritize reproducibility and protection of the canonical market history.

**How to apply:** Unset provider credentials for validation and use mocked LLM providers. Preserve raw files during data updates. If `origin` is absent, report that publishing was skipped instead of inventing a remote.
