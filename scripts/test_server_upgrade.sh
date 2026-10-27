#!/usr/bin/env bash
# Fast upgrade for staff.ex-strategy.com (git + venv source deploy).
# Run on the test host as root from /opt/staffdeck-src (or set STAFFDECK_SRC).
#
# Typical:
#   cd /opt/staffdeck-src && git pull --ff-only && bash scripts/test_server_upgrade.sh
# Optional: SKIP_FRONTEND=1 when only backend Python changed.
set -euo pipefail

SRC="${STAFFDECK_SRC:-/opt/staffdeck-src}"
DATA_DIR="${ULTRARAG_DATA_DIR:-/var/lib/staffdeck}"
VENV_PY="${SRC}/backend/.venv/bin/python"
NODE_BIN="${NODE_BIN:-}"

cd "$SRC"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing venv at $VENV_PY — run first-install steps in docs/test-server-deploy.md" >&2
  exit 1
fi

echo "==> [1/4] Sync backend deps (editable install)"
"$VENV_PY" -m pip install --disable-pip-version-check -e "backend" -q

if [[ "${SKIP_FRONTEND:-0}" != "1" ]]; then
  echo "==> [2/4] Build frontend"
  if [[ -z "$NODE_BIN" ]]; then
    if [[ -x /opt/staffdeck-toolchain/node/bin/npm ]]; then
      export PATH="/opt/staffdeck-toolchain/node/bin:$PATH"
    elif command -v npm >/dev/null 2>&1; then
      :
    else
      echo "npm not found; install Node 20+ or set PATH to /opt/staffdeck-toolchain/node/bin" >&2
      exit 2
    fi
  else
    export PATH="$(dirname "$NODE_BIN"):$PATH"
  fi
  npm --prefix frontend-enterprise ci --no-audit --no-fund
  npm --prefix frontend-enterprise run build
else
  echo "==> [2/4] SKIP_FRONTEND=1 — keeping existing frontend-enterprise/dist"
fi

echo "==> [3/4] Record deploy tip"
mkdir -p "$DATA_DIR"
git rev-parse HEAD >"$DATA_DIR/DEPLOY_SHA"
git log -1 --oneline >"$DATA_DIR/DEPLOY_TIP"
date -u +%Y-%m-%dT%H:%M:%SZ >"$DATA_DIR/DEPLOYED_AT"

echo "==> [4/4] Restart staffdeck"
systemctl restart staffdeck
sleep 2
systemctl --no-pager --full status staffdeck | head -20
curl -sS --max-time 10 "http://127.0.0.1:5173/api/health" || true
echo
echo "Deployed $(cat "$DATA_DIR/DEPLOY_TIP") at $(cat "$DATA_DIR/DEPLOYED_AT")"
