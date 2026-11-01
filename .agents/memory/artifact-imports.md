---
name: Artifact imports
description: Replacing source files in a Replit workspace without losing registered app artifacts and their managed workflows.
---

When importing a repository into a Replit workspace, avoid removing a registered artifact directory while syncing the root. Removing it can unregister its artifact and managed workflow even if the directory is copied back afterward.

**Why:** Replit tracks artifact registration alongside the artifact directory; deleting the directory during replacement can remove the app from the workspace.

**How to apply:** Keep registered artifact directories in place when copying source over them, or re-register after import. Stage repository clones outside the workspace when they contain artifact manifests, since manifests inside the workspace can be auto-registered. Check `listArtifacts` after import; if manifests share a preview path, update the secondary path through `verifyAndReplaceArtifactToml` before starting workflows. Restart a re-added workflow when its run command changes.

**Why:** Besides registration being tied to artifact directories, Replit validates preview paths across imported artifacts and can register temporary staged copies.
