---
name: Public repository downloads
description: Behavior of direct GitHub downloads in this Replit project.
---

Direct HTTPS git clones of public GitHub repositories may be rewritten to `github.replit.internal` and fail if the GitHub connection is disconnected. A public source archive at `https://codeload.github.com/<owner>/<repo>/zip/refs/heads/<branch>` succeeded without GitHub MCP.

**Why:** The environment routes GitHub clone traffic through Replit's proxy, while public archive downloads can use a direct public endpoint.

**How to apply:** For a public repository import when the user opts out of GitHub MCP, try its codeload archive and preserve registered Replit artifact directories while syncing.
