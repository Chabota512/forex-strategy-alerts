---
name: Public GitHub repository imports
description: An environment-specific fallback when direct Git clone is blocked by the Replit proxy.
---

**Rule:** Public GitHub clone traffic may be rewritten through Replit's internal Git proxy and fail when that connection is unavailable; a direct public archive download can still work.

**Why:** A direct clone of a public repository failed through the disconnected proxy, while its public codeload archive downloaded successfully.

**How to apply:** When a public-repository import is allowed through standard CLI or direct download and clone is blocked, try the public archive endpoint without using GitHub MCP.
