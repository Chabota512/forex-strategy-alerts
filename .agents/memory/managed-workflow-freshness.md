---
name: Managed workflow freshness
description: A managed artifact service may continue running an old command after its configuration changes.
---

After an artifact service's configured run command changes, its existing process may continue serving until the managed workflow is explicitly restarted. A "running" status alone does not prove the new command is active.

**Why:** The API route was configured in the Python service, but requests still reached a previously running Express process and returned 404 until restart.

**How to apply:** After a workflow command change, restart that exact managed workflow and confirm the service identity or expected endpoint response before debugging application routes.
