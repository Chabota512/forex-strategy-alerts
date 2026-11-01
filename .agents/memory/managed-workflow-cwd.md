---
name: Managed workflow working directories
description: How to form relative paths in managed artifact service commands.
---

**Rule:** Resolve artifact workflow paths from the owning artifact directory; do not assume commands start at the workspace root.

**Why:** Imported frontend and API startup commands failed with root-relative paths, then started once their paths were relative to their service directories.

**How to apply:** Before starting an artifact workflow, derive `cd` paths from the service's artifact folder, especially when the source app is nested.
