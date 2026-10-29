# 内部测试：别等生产发版

生产仍是 RapidStaff `https://aistaff.numwayai.com`，当前 **0.1.3**。刚合 main：PR 10（URL 路径占位符）、PR 11（认证配置）。无独立测试服；生产无 Playwright MCP。

仓库 `sauger/AIStaff`。本文只说明怎么更快地在本机验证已合入的改动，不改变生产发版路径，也不实现新的部署工具。

---

## 1. 现在生产怎么发、为什么慢

一句话：**离机打 Linux `.deb` → scp → `dpkg -i` → systemd**。生产机不编译，不用 Docker，不用 `scripts/dev_up.sh`。

| 步骤 | 实际做什么 | 慢在哪 |
| --- | --- | --- |
| 打 tag 走 CI | `.github/workflows/release.yml` 一次编 **4 平台**：macOS arm64、macOS Intel、Ubuntu、Windows | 3/4 跟 aistaff **无关**；mac 还可能公证 |
| Linux 包 | `packaging/build_linux.sh`：前端 `npm ci` + `build` → 便携 CPython 3.11 → **清空重建** `backend/.venv` → **PyInstaller 冻整包** → 打进 SRT+Node 沙箱 → `dpkg-deb` → **再打一份 AppImage** | 冻包 + 沙箱 + AppImage。生产 **只用 `.deb`**，AppImage 白打 |
| 上传 | `scp` 到 `/opt/staffdeck-releases/`（上次 0.1.3 包 **118MB**，压缩 scp **~114s**） | 体积大，不是卡点主力 |
| 安装 | `dpkg -i` → `systemctl restart staffdeck` | 这几分钟其实快 |
| 入口 | 进程只听 `127.0.0.1:5173`；1Panel OpenResty HTTPS 反代 | 跟代码无关，但每次发版都要保这条链 |

上次 0.1.3 是 **云 VM 上直接跑 `build_linux.sh`**，没等四平台 Release。即便如此，冻包仍重。仓库禁止在 `aistaff.numwayai.com` 上编（共享机、盘紧、线上已在跑）。

**内部测 PR 10/11，不必走这条链。** 那两处是应用层（HTTP 工具 URL 模板、认证配置 UI/API），源码单端口就能验。

---

## 2. 更快的办法（按速度，保真从低到高）

仓库 **没有** 运行时 Dockerfile / compose。`packaging/_docker_test_linux.sh` 是给 ARM 机 **交叉测 Linux 打包** 的，会更慢，不要当部署。生产明确不改 Docker 栈。

### A. 本机单端口 — 已有，最快

```bash
# 需要 Python 3.11+、Node 20；已有 backend/.venv 会复用
scripts/dev_up.sh --detach
scripts/dev_status.sh
# 打开 http://127.0.0.1:5173/chat/  （健康检查 /api/health）
# 停：scripts/dev_down.sh
```

干什么：`npm run build` 前端 → `uvicorn single_port_app:app` 同一端口出 SPA+API（和冻包入口同一个 app）。首次会补依赖和沙箱运行时；之后主要是一次前端 build。

适合：PR 10 占位符、PR 11 认证配置页 / HTTP 工具绑定 / API 自动登录。

少改：0。

### B. 源码 uvicorn，跳过包装脚本 — 已有，几乎一样快

```bash
npm --prefix frontend-enterprise run build
backend/.venv/bin/uvicorn single_port_app:app --host 127.0.0.1 --port 5173
```

比 A 少一层 supervisor。无自动补依赖、无 `FORCE_PORTS`、无 `.dev` 日志。适合已经装好 venv 的人。

### C. 只打 Linux 冻包，装到「随便一台 x86_64」，不上 aistaff — 仓库已有脚本，小改

```bash
# 必须 x86_64 Linux（不要在生产机）
export VERSION=0.1.4-internal
bash packaging/build_linux.sh
# 产物：packaging/out/StaffDeck-linux-x86_64.deb
# 也可直接跑未打 dpkg 的冻包（Windows CI 已这样 smoke）：
STAFFDECK_HEADLESS=1 packaging/out/staffdeck/staffdeck
```

比完整生产快在：

- 不必打 tag，不必编 mac/win
- 不必 scp 上生产、不必动 OpenResty
- 可忽略 AppImage（脚本现在总会打；加 `SKIP_APPIMAGE=1` 是很小的补丁，现在没有这个开关）
- CI 已有 `workflow_dispatch`，但 **矩阵仍是 4 平台**；要更快需加「只跑 ubuntu」——也是小改

上次就是云 VM 走这条编出 0.1.3。仍要付 PyInstaller 时间，只是少掉三台 runner + 生产安装窗口。

### D. 临时预发机 — 仓库没有，运维小、不写代码

没有测试服。可选：

- 另开一台便宜 x86_64，装 C 的 `.deb` + systemd，**不要**抢生产的 5173 / OpenResty
- **不要**在 aistaff 同机再起一套（盘紧、端口、root 共享）
- 需要给别人点链接时：SSH 隧道或内网 IP，而不是新域名（新域名又要证书，接近生产发版成本）

Docker 不当选项：没有现成运行镜像；硬加等于新栈，保真度还低于 `.deb`。

---

## 3. 各方案验不了什么（对真实生产）

PR 10/11 **能**在 A/B 验：控制台文案与表单、HTTP 工具 `{name}` 替换、认证配置 API 类型、绑定后注入头、一次自动登录重试。网页类型登录依赖 Playwright；**生产也没装 Playwright MCP**，所以即使用 `.deb` 上 aistaff 也验不了网页登录梯子。

| | A 本机 `dev_up` | B 源码 uvicorn | C Linux 冻包/跳过安装器 | D 独立机 `.deb` | 真生产 `.deb` |
| --- | --- | --- | --- | --- | --- |
| SPA+API 同源、PR 10/11 业务 | 能 | 能 | 能 | 能 | 能 |
| 生产 SQLite / TokenOp 数据 / 现网 `.env` | 否 | 否 | 否 | 否（除非拷库，别拷） | 是 |
| PyInstaller 漏 import、冻包路径 | 否 | 否 | 能 | 能 | 能 |
| `dpkg` 覆盖 `/opt/staffdeck`、数据目录不丢 | 否 | 否 | 跑裸二进制则否 | 能 | 能 |
| systemd + 只听 `127.0.0.1:5173` | 否 | 否 | 手设 env 才像 | 能 | 能 |
| OpenResty TLS、CORS、`STAFFDECK_PUBLIC_URL`、SSE 超时 | 否 | 否 | 否 | 否（除非再搭反代） | 是 |
| 同机 New API / token-channel 互扰 | 否 | 否 | 否 | 否 | 是 |
| Playwright MCP | 全否 | 全否 | 全否 | 全否 | **生产也没有** |

默认管理员本机是 `admin`/`admin`；生产是工作台自建账号，无预置口令。本机 `APP_SECRET` 和线上不是一把，渠道密文解不开——**不要把生产 `.env` 拷到笔记本。**

---

## 4. 内部测试阶段默认选 A

**先 `git pull` main，本机 `scripts/dev_up.sh --detach`，在 http://127.0.0.1:5173 点 PR 10/11。**

理由：这两项不依赖冻包、dpkg、TLS。A 已是仓库官方开发路径，和线上同一 `single_port_app.py`。等功能在本机过了，再考虑一次 **C（只 Linux 冻包）** 防 PyInstaller 漏模块；确认要给外部用户用，才走完整生产 `.deb`。

不要为了「快一点上 aistaff」去：在生产机编译、`dev_up` 顶掉 5173、新起 Docker、为内部测打四平台 tag。
