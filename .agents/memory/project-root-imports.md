---
name: Replit project-root imports
description: Safely replacing starter files with downloaded repository content in a Replit workspace.
---

**Rule:** Preserve `.git`, `.conversation`, and `.cache` when replacing a fresh workspace's starter files with a repository working tree.

**Why:** `.conversation` is a project-managed mount; deleting it can interrupt cleanup even when the source project is fresh.

**How to apply:** Inspect the workspace root before cleanup, and copy repository content without replacing the new project's own Git metadata.
