# Packaging notes

## Sandbox runtime cache

`packaging/fetch_sandbox_runtime.py` downloads a pinned Node archive and installs `@anthropic-ai/sandbox-runtime`. `build_linux.sh`, `build_macos.sh`, and `build_windows.ps1` delete `packaging/sandbox_runtime` on every run and then call the fetcher. That directory is only the copy staged into the app. Reusable downloads live in a durable cache so a cleaned staging directory or temp dir does not force another fetch.

- Default cache: `packaging/cache/` (gitignored)
- Override: `STAFFDECK_PACKAGING_CACHE=/path/outside/the/worktree`

Layout:

- `node/node-<version>-<platform>.tar.gz` or `.zip` — the Node archive. It is reused only when its SHA256 matches the pin in `NODE_SHA256`, or `STAFFDECK_NODE_SHA256` when that override is set. A different `STAFFDECK_NODE_VERSION` uses a different filename, so the previous archive is not reused.
- `sandbox-runtime/<key>/` — the extracted Node binary plus the npm bundle, after lockfile verification and the StaffDeck network patch. The key covers Node version, archive hash, OS/CPU, `sandbox-runtime-package.json`, the lockfile, and the patch hashes. Reuse also requires every shipped file to match the manifest stored after that install, and `bin/node` to match the executable inside the hash-pinned archive. The next build copies this tree back into `packaging/sandbox_runtime` and does not contact the network.
- `npm/` — npm's cache, used only when the prepared bundle must be rebuilt.

A missing, corrupt, or mismatched cache entry is ignored and fetched again. CI and cloud builds with an empty cache keep the previous download path.

Node URLs are tried in order. The first archive whose SHA256 matches the pin is stored. The version and filename are appended (`/<version>/<filename>`):

1. `STAFFDECK_NODE_DIST_BASE` when set, for example `https://cdn.npmmirror.com/binaries/node`
2. `https://nodejs.org/dist`
3. `https://cdn.npmmirror.com/binaries/node`
4. `https://mirrors.aliyun.com/nodejs-release`

On a demo host with slow or blocked access to `nodejs.org`, point the cache somewhere that clean scripts do not delete and prefer a China mirror:

```bash
export STAFFDECK_PACKAGING_CACHE="$HOME/.cache/staffdeck-packaging"
export STAFFDECK_NODE_DIST_BASE="https://cdn.npmmirror.com/binaries/node"
```

The second build with the same Node and sandbox-runtime versions then reuses both the tarball and the prepared bundle.
