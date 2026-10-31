---
name: Repository refresh preference
description: How the user wants public repository refreshes handled in this project.
---

When refreshing this project's files from a repository, apply the latest snapshot directly without comparing or reviewing versions, preserve user-uploaded files that are not in the repository, and regenerate API clients before typechecking.

**Why:** the user explicitly repeated that they want a direct update without comparison.

**Why:** a refreshed snapshot can include an OpenAPI spec that no longer matches stale generated clients; regenerating them prevents widespread missing-export type errors.

**How to apply:** before replacing project files, identify and preserve user uploads outside the repository snapshot; do not diff source versions unless asked. After a refresh, run `pnpm --filter @workspace/api-spec run codegen` before the app typecheck.
