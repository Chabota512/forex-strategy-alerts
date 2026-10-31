---
name: Public GitHub archive downloads
description: Downloading public repositories when ordinary Git access is unavailable.
---

When shell Git access to `github.com` is redirected to `github.replit.internal` and fails because the Replit GitHub connection is disconnected, a direct public archive download from `codeload.github.com` may still work without using the GitHub integration.

The GitHub API integration's `zipball` endpoint can also fail when the Replit proxy does not follow GitHub's archive redirect. For file-by-file imports, the Contents endpoint works with `Accept: application/vnd.github.raw+json`; verify one raw response before downloading the full tree.

**Why:** The Replit GitHub API proxy rejected the archive redirect, while raw Contents requests succeeded.

**How to apply:** For public snapshots that do not need Git history or authenticated access, try the direct `codeload.github.com` archive. If the API integration is required, enumerate the tree and fetch raw file contents. Do not use either path to bypass access controls or retrieve private repositories.
