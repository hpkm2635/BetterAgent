# 便携"绿化包"构建与分发指南

本文档描述如何把 BetterAgent 打包成一个可以在没有装 Python / Node.js / Go / Docker 的 Windows 机器上直接解压运行的独立目录，以及这个包本身覆盖和不覆盖哪些功能。

## 覆盖范围

**包含在便携包内**（全部免安装，解压即用）：
- Go core（`bin/betteragent_core.exe`）
- NATS（`bin/nats-server.exe`）
- Qdrant 向量数据库（`bin/qdrant.exe`）
- 一份预装好全部依赖的便携 Python 3.12 运行时（`bin/python-portable/`），驱动 8 个 Python 微服务 + 后台管理接口
- 两个前端的生产构建产物（管理面板 `admin/frontend/dist/`、数字人前端 `frontend/apps/stage-web/dist/`），由一个零依赖的标准库静态/反向代理服务器托管

**不包含，需要对方自己另外准备**：
- **FunASR**（语音识别）——几 GB 的 ML 推理服务容器，跟今天的部署方式一样，需要对方自己起（`deploy/docker-compose.yml` 里的 `funasr` 服务，或者不启用语音输入功能）。
- **GPT-SoVITS**（语音合成）——同理，一直就是外部进程，这次也不例外。
- **Redis**——没有官方 Windows 发行版。不带这项：`services/memory/short_term_buffer.py`/`user_profile.py` 已经自带"连不上就退化成纯内存态"的兜底逻辑，功能完整可用，代价是每次重启进程后短期对话记忆（当前会话的上下文缓冲、未持久化的用户画像增量）会清空——不影响 Qdrant 里的长期记忆（人物关系、历史事件摘要等）。

## 在构建机器上打包

构建机器（可以是这台开发机，也可以是专门的 CI/打包机）需要装好 Go、Node.js + pnpm、npm，并且能访问外网（下载 NATS/Qdrant/embeddable Python 官方发行版）。**必须在 Windows 上跑**——脚本下载的都是 Windows 专用二进制（`.exe`、embeddable Python 的 Windows 发行版），在其它系统上跑这个脚本产出的包无法在目标 Windows 机器上使用。

```powershell
python scripts\build_portable_package.py
```

这一步会依次：
1. `go build` Go core（复用 `runner.py` 已有的构建逻辑，检测源码是否比现有二进制新，避免用过期二进制）。
2. 下载 `bin/nats-server.exe`、`bin/qdrant.exe`（若已存在则跳过）。
3. 构建 `bin/python-portable/`：下载 python.org 官方 embeddable 发行版，启用 `site`/`pip`，装好根 `requirements.txt` + 全部子服务 `requirements.txt` 的并集（若已存在则跳过——重新打包不需要每次都重新下载/装一遍）。
4. `pnpm build`（`frontend/`，数字人前端）、`npm run build`（`admin/frontend/`，管理面板）。
5. 把 `bin/`、`services/`、`shared/`、`admin/backend/`、`config/`、`scripts/`、`runner.py`、`.env.example`，以及两个前端刚构建出的 `dist/`，组装进 `portable_package/` 目录，并写入一个 `START.bat` 启动脚本。

产出是一个**目录**，不是 zip——便携 Python + Qdrant + 两份前端构建产物加起来体积不小，脚本不做二次压缩；需要传给别人时自己用 7-Zip/WinRAR 打包即可。

## 分发与在目标机器上运行

1. 把整个 `portable_package/` 目录拷给对方（或者压缩后发过去，对方解压）。
2. 对方把 `.env.example` 复制一份改名成 `.env`，至少填好 `NATS_USER`/`NATS_PASSWORD`/`QDRANT_API_KEY`/`ADMIN_SECRET_KEY`（`start_qdrant_if_needed()`/`start_nats_if_needed()` 都会在这些值缺失时直接拒绝以匿名模式启动服务，而不是静默弱化安全性）。
3. 双击 `START.bat`。它会用 `bin\python-portable\python.exe` 跑 `runner.py`——`runner.py` 通过 `is_portable_mode()`（检测 `bin/python-portable/python.exe` 是否存在）自动识别出这是便携包而不是开发者的 `.venv` checkout，两个前端会走静态托管（`scripts/portable_static_server.py`）而不是尝试 `pnpm run dev`/`npm run dev`（目标机器根本没有 Node.js）。
4. 浏览器打开 `http://localhost:5173/`（数字人对话界面）和 `http://localhost:8095/`（后台管理面板，登录密码即 `.env` 里的 `ADMIN_SECRET_KEY`）。

## 已知限制

- 只验证过 Windows。埋在便携 Python/NATS/Qdrant 里的都是 Windows 专用二进制。
- 如果对方想要完整的语音输入/输出体验，仍然需要自己另外起 FunASR 和 GPT-SoVITS（参考 `deploy/docker-compose.yml` 里 `funasr` 服务的配置，以及 `services/tts/gpt_sovits_client.py` 顶部注释里记的默认 endpoint `http://127.0.0.1:19888/tts`）——没起这两个的话，文字对话、校园知识库、日程等其余功能不受影响，只是没有语音。
- 重启一次 `START.bat` 会清空短期对话记忆（见上文 Redis 部分），但不影响 Qdrant 里持久化的长期记忆。
