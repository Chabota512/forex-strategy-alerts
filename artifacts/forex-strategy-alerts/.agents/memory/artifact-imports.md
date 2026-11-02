---
name: Artifact imports
description: Replacing source files in a Replit workspace without losing registered app artifacts and their managed workflows.
---

When importing a repository into a Replit workspace, avoid removing a registered artifact directory while syncing the root. Removing it can unregister its artifact and managed workflow even if the directory is copied back afterward.

**Why:** Replit tracks artifact registration alongside the artifact directory; deleting the directory during replacement can remove the app from the workspace.

**How to apply:** Keep the registered artifact directory in place when copying source over it, or re-register the artifact after the repository import before starting its workflow. If an artifact is re-added under the same name but uses a different run command, restart its managed workflow so the new command replaces any still-running process.
