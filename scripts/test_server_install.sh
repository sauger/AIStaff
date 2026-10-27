#!/usr/bin/env bash
# First-time source install for staff.ex-strategy.com (OpenCloudOS / rpm host).
# Prefer this over .deb on the test host: upgrades are git pull + this repo's
# scripts/test_server_upgrade.sh (no PyInstaller freeze).
#
# Prerequisites: Python 3.11+, git, enough disk; run as root.
# Env:
#   STAFFDECK_SRC=/opt/staffdeck-src
#   STAFFDECK_PUBLIC_URL=https://staff.ex-strategy.com
#   STAFFDECK_GIT_URL=git@github.com:sauger/AIStaff.git  (or https)
#   STAFFDECK_GIT_REF=main   # or a tip SHA / branch that includes desired PRs
set -euo pipefail

SRC="${STAFFDECK_SRC:-/opt/staffdeck-src}"
DATA_DIR="${ULTRARAG_DATA_DIR:-/var/lib/staffdeck}"
PUBLIC_URL="${STAFFDECK_PUBLIC_URL:-https://staff.ex-strategy.com}"
GIT_URL="${STAFFDECK_GIT_URL:-}"
GIT_REF="${STAFFDECK_GIT_REF:-main}"
NODE_VERSION="${NODE_VERSION:-20.19.5}"
TOOLCHAIN="/opt/staffdeck-toolchain"

echo "==> Data + release dirs"
mkdir -p "$DATA_DIR" /opt/staffdeck-releases "$TOOLCHAIN"
chmod 700 "$DATA_DIR"

if [[ ! -d "$SRC/.git" ]]; then
  if [[ -z "$GIT_URL" ]]; then
    echo "Set STAFFDECK_GIT_URL to clone, or place a git checkout at $SRC" >&2
    exit 1
  fi
  echo "==> Clone $GIT_URL → $SRC"
  git clone "$GIT_URL" "$SRC"
fi
cd "$SRC"
git fetch --all --tags
git checkout --force "$GIT_REF"
git reset --hard "$GIT_REF" 2>/dev/null || git reset --hard "origin/$GIT_REF"

if [[ ! -x "$TOOLCHAIN/node/bin/node" ]]; then
  echo "==> Portable Node ${NODE_VERSION}"
  tmp="$(mktemp -d)"
  curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
    -o "$tmp/node.tar.xz"
  tar -xJf "$tmp/node.tar.xz" -C "$tmp"
  rm -rf "$TOOLCHAIN/node"
  mv "$tmp/node-v${NODE_VERSION}-linux-x64" "$TOOLCHAIN/node"
  rm -rf "$tmp"
fi
export PATH="$TOOLCHAIN/node/bin:$PATH"
node -v
npm -v

echo "==> Python venv + backend"
python3.11 -m venv backend/.venv
backend/.venv/bin/python -m pip install --disable-pip-version-check -U pip
backend/.venv/bin/python -m pip install --disable-pip-version-check -e "backend"

echo "==> Sandbox runtime (reviewed SRT + Node)"
backend/.venv/bin/python packaging/fetch_sandbox_runtime.py packaging/sandbox_runtime

echo "==> Frontend build"
npm --prefix frontend-enterprise ci --no-audit --no-fund
npm --prefix frontend-enterprise run build

if [[ ! -f "$DATA_DIR/.env" ]]; then
  echo "==> Creating $DATA_DIR/.env (edit secrets before production use)"
  APP_SECRET_VAL="$(openssl rand -hex 32)"
  CHANNEL_SECRET_VAL="$(openssl rand -hex 32)"
  cat >"$DATA_DIR/.env" <<EOF
APP_SECRET=${APP_SECRET_VAL}
CHANNEL_SECRET=${CHANNEL_SECRET_VAL}
DATABASE_URL=sqlite:////var/lib/staffdeck/staffdeck.db
CORS_ORIGINS=${PUBLIC_URL}
TOOL_BASE_URL=${PUBLIC_URL}
EXTERNAL_TASK_CALLBACK_BASE_URL=${PUBLIC_URL}
STAFFDECK_PUBLIC_URL=${PUBLIC_URL}
MODEL_THINKING_MODE=disabled
EOF
  chmod 600 "$DATA_DIR/.env"
fi

echo "==> network.json (public mode)"
ULTRARAG_DATA_DIR="$DATA_DIR" \
  backend/.venv/bin/python -m desktop_launcher setup --mode public --port 5173 \
  --public-url "$PUBLIC_URL" || true

echo "==> systemd unit"
cat >/etc/systemd/system/staffdeck.service <<EOF
[Unit]
Description=StaffDeck single-port app (staff.ex-strategy.com)
After=network.target

[Service]
Type=simple
WorkingDirectory=${SRC}/backend
Environment=STAFFDECK_HEADLESS=1
Environment=ULTRARAG_HOST=0.0.0.0
Environment=ULTRARAG_PORT=5173
Environment=STAFFDECK_PUBLIC_URL=${PUBLIC_URL}
Environment=ULTRARAG_DATA_DIR=${DATA_DIR}
Environment=ULTRARAG_PORT_RANGE_START=5173
Environment=ULTRARAG_PORT_RANGE_END=5173
EnvironmentFile=-${DATA_DIR}/.env
ExecStart=${SRC}/backend/.venv/bin/python -m desktop_launcher
Restart=on-failure
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable staffdeck

git rev-parse HEAD >"$DATA_DIR/DEPLOY_SHA"
git log -1 --oneline >"$DATA_DIR/DEPLOY_TIP"
date -u +%Y-%m-%dT%H:%M:%SZ >"$DATA_DIR/DEPLOYED_AT"

echo "==> Start"
systemctl restart staffdeck
sleep 3
curl -sS --max-time 15 "http://127.0.0.1:5173/api/health" || true
echo
echo "Installed tip: $(cat "$DATA_DIR/DEPLOY_TIP")"
echo "Next: point nginx staff.ex-strategy.com at http://172.17.0.1:5173 then reload."
echo "Upgrade later: cd $SRC && git pull --ff-only && bash scripts/test_server_upgrade.sh"
