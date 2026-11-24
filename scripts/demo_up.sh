#!/usr/bin/env bash
# One-command AIStaff demo: seed demo data, then build and start the single-port app.
#
#   scripts/demo_up.sh            # seed (idempotent) + start
#   scripts/demo_up.sh --reset    # clear runtime data first, then seed + start
#   scripts/demo_up.sh --seed-only
#
# 需求：backend/.venv 已安装依赖；demo/prod 档需 DEMO_ADMIN_PASSWORD 与 WORKFLOW_RUNTIME_ENABLED=true。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN=""
for candidate in "$ROOT_DIR/backend/.venv/bin/python" "${PYTHON:-}" python3 python; do
  [[ -n "$candidate" ]] || continue
  if "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
    PYTHON_BIN="$candidate"
    break
  fi
done
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Python 3.11 or newer is required." >&2
  exit 1
fi

RESET=0
SEED_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --reset) RESET=1 ;;
    --seed-only) SEED_ONLY=1 ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

export DEPLOYMENT_PROFILE="${DEPLOYMENT_PROFILE:-demo}"
if [[ "$DEPLOYMENT_PROFILE" == "dev" ]]; then
  echo "提示：DEPLOYMENT_PROFILE=dev 会保留 admin/admin 默认口令，仅限本机演示。"
else
  export WORKFLOW_RUNTIME_ENABLED="${WORKFLOW_RUNTIME_ENABLED:-true}"
  if [[ -z "${DEMO_ADMIN_PASSWORD:-}" ]]; then
    echo "缺少 DEMO_ADMIN_PASSWORD：$DEPLOYMENT_PROFILE 档不会生成默认口令。" >&2
    echo "请先导出 DEMO_ADMIN_PASSWORD（和可选的 DEMO_USER_PASSWORD）再运行。" >&2
    exit 1
  fi
fi

if [[ "$RESET" == "1" ]]; then
  echo "== 清空演示运行数据 =="
  "$PYTHON_BIN" backend/scripts/demo_reset.py
  echo
fi

echo "== 准备演示数据 =="
"$PYTHON_BIN" backend/scripts/demo_seed.py

if [[ "$SEED_ONLY" == "1" ]]; then
  exit 0
fi

echo
echo "== 构建并启动 =="
"$ROOT_DIR/scripts/dev_up.sh" --detach
"$ROOT_DIR/scripts/dev_status.sh"
echo
echo "演示入口：http://127.0.0.1:5173 （账号见 backend/.venv 数据目录下的 demo_credentials.txt 或 DEMO_ADMIN_PASSWORD）"
echo "收尾：scripts/dev_down.sh；下次演示前：scripts/demo_up.sh --reset"
