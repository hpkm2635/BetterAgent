# BetterAgent 后台管理系统（Admin Panel）

B 端后台管理控制台，对应 `docs/API-CONTRACT.md` 的 **接口契约二**。用于管理数字人角色（Persona）、
用户（User）、会话记录（Session）、日程提醒（Schedule，代理至 companion）与知识库（Knowledge Base，代理至 campus_kb）。

- **Admin API 端口**: `8094`（FastAPI）
- **Admin UI Dev 端口**: `8095`（Vite dev server）

```
admin/
  backend/
    main.py           # uvicorn 入口（自包含，不 import core/ / shared/）
    requirements.txt
  frontend/           # 独立 Vue3 项目，不依赖 BetterAgent frontend/
  README.md
```

## 启动 backend

> 要求 Python >= 3.12。依赖独立声明在 `admin/backend/requirements.txt`，不依赖主仓库依赖。

```bash
cd admin/backend
python -m venv .venv

# Windows (Git Bash / PowerShell)
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python main.py

# macOS / Linux
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

或直接用 uvicorn 启动：

```bash
cd admin/backend
uvicorn main:app --host 0.0.0.0 --port 8094
```

启动后访问 `http://localhost:8094/health` 应返回：

```json
{ "status": "ok", "service": "admin_backend" }
```

### 可选环境变量

| 变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `ADMIN_PORT` | `8094` | Admin API 监听端口 |
| `PERSONA_DIR` | `config/persona`（相对仓库根） | 人设 YAML 目录 |
| `ADMIN_DB_PATH` | `admin/backend/admin.db` | 软删除标记 SQLite 路径 |
| `REDIS_URL` | `redis://127.0.0.1:6379` | 用户画像 / 会话历史 Redis |
| `REDIS_PASSWORD` | 空 | Redis 密码（可从 `.env` 读取） |
| `CAMPUS_KB_URL` | `http://127.0.0.1:8093` | campus_kb 服务地址 |
| `COMPANION_URL` | `http://127.0.0.1:8096` | companion 服务地址（日程提醒代理） |
| `ADMIN_SECRET_KEY` | 空 | Admin API 访问令牌（可选） |

> 敏感值（如 `REDIS_PASSWORD`）从 `admin/backend/.env` 读取，不硬编码进代码。
>
> **BYOK 热刷新（2.7）**：PATCH `/api/admin/config` 发布 `agent.config.reloaded` 时，
> NATS 连接凭据读取优先级为 —— 环境变量 > 仓库根 `.env`（`NATS_USER` / `NATS_PASSWORD`），
> 连接地址为环境变量 `NATS_URL` > `config/config.yaml` 的 `infrastructure.nats_url`。

### 访问令牌（可选）

设置 `admin/backend/.env` 中的 `ADMIN_SECRET_KEY` 后，所有 `/api/admin/*` 接口都要求携带令牌，
否则返回 `401 {"error": "unauthorized"}`。支持两种请求头：

```
X-Admin-Token: <secret>
Authorization: Bearer <secret>
```

- 留空（默认）时校验关闭，便于本地开发。
- Admin Web UI 的 Vite 代理会从 `admin/frontend/.env` 读取同名 `ADMIN_SECRET_KEY` 并注入
  `X-Admin-Token`，因此两者需配置为相同值；令牌只存在于代理层，不会进入浏览器 JS。
- `/health` 始终公开，不受令牌保护。

## 启动 frontend

> 要求 Node.js >= 18。

```bash
cd admin/frontend
npm install
npm run dev
```

启动后访问 `http://localhost:8095` 查看管理控制台首页。Vite 已将 `/api` 与 `/health` 代理到
`http://127.0.0.1:8094`，因此需先启动 backend。

生产构建：

```bash
cd admin/frontend
npm run build    # 产物输出到 dist/
```

## API 一览

### 2.1 人设管理

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/admin/personas` | 列出所有人设（id/name/tts_provider/voice_id） |
| GET | `/api/admin/personas/{persona_id}` | 获取单个人设完整 YAML（JSON） |
| PATCH | `/api/admin/personas/{persona_id}` | 部分更新（白名单 6 字段，ruamel 原地更新保留注释与顺序） |

PATCH 白名单字段：`name`、`appearance`、`base_prompt`、`sleepy_prompt`、`knowledge_scope`、`forbidden_topics`。
修改白名单外字段返回 `400 {"error": "Forbidden field: <field>"}`。

### 2.2 用户管理（只读 + 软删除）

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/admin/users` | 列出用户 |
| GET | `/api/admin/users/{user_id}` | 获取单个用户 |
| DELETE | `/api/admin/users/{user_id}` | 软删除用户 |

画像数据来自 Redis `betteragent:profile:{user_id}`（主服务写入的 key，兼容旧 `user_profile:{user_id}`）；软删除标记落在独立 SQLite 表（`admin.db`）。
**绝不修改** Redis 中的对话历史 key（`short_term:{chat_id}`）。

### 2.3 会话记录查看（只读）

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/admin/sessions?chat_id={chat_id}&limit=50&offset=0` | 查看会话历史 |

Redis 不可用时返回 `{"sessions": [], "total": 0}`，不崩溃。

### 2.4 知识库管理（代理）

| 方法 | 路径 | 代理到 |
| :--- | :--- | :--- |
| POST | `/api/admin/kb/ingest` | `POST http://127.0.0.1:8093/api/kb/ingest` |
| GET | `/api/admin/kb/search` | `POST http://127.0.0.1:8093/api/kb/search` |

campus_kb 不可用时返回 `503 {"error": "campus_kb service unavailable"}`。

### 2.5 健康检查

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/health` | `{"status": "ok", "service": "admin_backend"}` |

### 2.6 日程提醒管理（代理）

| 方法 | 路径 | 代理到 |
| :--- | :--- | :--- |
| GET | `/api/admin/schedules?chat_id={chat_id}` | `GET http://127.0.0.1:8096/api/schedule/list?chat_id={chat_id}` |
| POST | `/api/admin/schedules` | `POST http://127.0.0.1:8096/api/schedule/add` |
| DELETE | `/api/admin/schedules/{schedule_id}` | `DELETE http://127.0.0.1:8096/api/schedule/{schedule_id}` |

日程数据由 companion 服务（:8096）持久化，并由其 APScheduler 负责到期触发；Admin 只做反向代理，
**不直接写 SQLite**（直接写入会导致 APScheduler 感知不到新日程、提醒不会触发）。
companion 不可用时返回 `503 {"error": "companion service unavailable"}`。

### 2.7 系统配置与 API 密钥管理（BYOK 模式）

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/admin/config` | 读取默认 Provider、网络代理与各 Provider 脱敏 key 状态 |
| PATCH | `/api/admin/config` | 更新 Provider key / model、默认 Provider、网络代理；写回根 `.env` 与 `config/config.yaml`，并发布 NATS `agent.config.reloaded` |
| POST | `/api/admin/config/test-key` | 连通性测试：返回 HTTP 延迟与可用模型列表（不保存配置） |

管理范围：`gemini` / `claude` / `qwen` 三个 LLM Provider（对应根 `.env` 的
`GEMINI_API_KEY` / `CLAUDE_API_KEY` / `QWEN_API_KEY` 与 `config/config.yaml` 的 `llm.<name>.model`）。

- 密钥仅保存在仓库根 `.env`，接口与前端均只返回脱敏形式（如 `AIzaSy***4x9`）。
- `config/config.yaml` / `.env` 不存在时，PATCH 会自动从 `config.yaml.example` / `.env.example` 播种。
- `agent.config.reloaded` 为全服务热刷新信号；消费端由技术总监在各服务内实现，Admin 只负责发布。
- PATCH 发布失败不阻断配置落盘，响应 `reloaded` 字段反映发布是否成功。
