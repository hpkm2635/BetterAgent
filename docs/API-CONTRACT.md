# BetterAgent — API 接口契约文档 (API-CONTRACT.md)

> **本文档是主分支唯一认可的接口规范。**  
> 所有 Feature Branch 的 PR 必须通过本文档描述的接口测试，才能被合并到 `main`。  
> 各组员只需保证自己模块对外的接口符合此规范，内部实现不限。

---

## 团队分工与 Feature Branch 规划

| 成员 | Feature Branch | 负责模块 | 核心交付 |
| :--- | :--- | :--- | :--- |
| **褚裕禄（技术总监）** | `main` / `feat/go-core-*` | Go Core、Frontend、部署、MCP工具 | 架构保障、代码评审、集成 |
| **谢自立** | `feat/admin-panel` | 任务5：后台管理系统 | Admin REST API + Admin Web UI |
| **冯文哲** | `feat/campus-kb` | 任务2：校园知识库 RAG | 向量入库服务 + KB Search HTTP API |
| **张劭哲（TBD）** | `feat/companion-tools` | 任务3补齐：SQL Agent / 日程提醒 / 任务推荐 | Tool 注册 + Companion REST API |

> **防污染原则**：
>
> - 组员 **绝对禁止** 修改以下目录/文件：`core/`、`runner.py`、`shared/`、`frontend/`、`config/config.yaml`
> - 组员 **只能新增** 自己 Feature 目录下的文件，或向 `services/` 新增独立子目录
> - `config/persona/*.yaml` 只允许 Admin 后端通过 PATCH 接口写入已有字段，禁止增删顶级 key
> - 跨模块集成只通过 **HTTP REST API**（首选）调用；若需触发 Agent 响应，通知技术总监添加 NATS 发布点

---

## 接口契约一：校园知识库 RAG 服务（冯文哲）

**Feature Branch**: `feat/campus-kb`  
**服务目录**: `services/campus_kb/`  
**启动入口**: `python -m services.campus_kb.main`（或 `uvicorn services.campus_kb.main:app`）  
**固定端口**: `8093`

### 1.1 知识库搜索

```
POST http://localhost:8093/api/kb/search
Content-Type: application/json
```

**Request**:
```json
{
  "query": "图书馆几点关门",
  "top_k": 5,
  "category": null
}
```

| 字段 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `query` | string | ✅ | 自然语言问题 |
| `top_k` | int | ❌ | 默认 5，最大 20 |
| `category` | string\|null | ❌ | `faq` \| `schedule` \| `resource` \| `service` \| null（不过滤） |

**Response 200**:
```json
{
  "results": [
    {
      "content": "图书馆周一至周五开放至22:00，周末20:00关闭。",
      "source": "lib_faq.md",
      "score": 0.87,
      "category": "faq"
    }
  ],
  "query": "图书馆几点关门",
  "total": 1
}
```

**Response 5xx**（服务内部错误，不能崩溃，必须返回 JSON）:
```json
{
  "error": "向量库连接失败",
  "results": [],
  "total": 0
}
```

### 1.2 知识库文档入库

```
POST http://localhost:8093/api/kb/ingest
Content-Type: application/json
```

**Request**:
```json
{
  "documents": [
    {
      "content": "string（文档正文）",
      "source": "string（来源标识，如文件名）",
      "category": "faq",
      "metadata": {}
    }
  ]
}
```

**Response 200**:
```json
{
  "ingested": 5,
  "failed": 0,
  "message": "OK"
}
```

### 1.3 健康检查

```
GET http://localhost:8093/health
→ 200  { "status": "ok", "service": "campus_kb" }
```

### 1.4 集成方式（技术总监侧完成，组员无需修改任何现有文件）

技术总监会在 `services/cognitive/tools/campus_kb_tool.py` 中以 HTTP 调用该接口，
并在 `ToolRegistry` 中注册，最终将命中片段注入 `ReasoningRequestPayload.rag_facts`。

### 1.5 PR 合并门控

- [ ] `GET /health` → `{"status": "ok"}`
- [ ] 先 `POST /api/kb/ingest` 写入 3 条测试文档，再 `POST /api/kb/search` 命中至少 1 条
- [ ] 服务在 `8093` 正常监听（TCP 可达），不依赖 BetterAgent 其他服务即可独立启动
- [ ] `services/campus_kb/README.md` 包含完整本地启动命令
- [ ] PR diff 不包含 `core/`、`shared/`、`runner.py`、`frontend/` 任何文件的修改

---

## 接口契约二：后台管理系统（谢自立）

**Feature Branch**: `feat/admin-panel`  
**服务目录**: `admin/`  
**Admin API 端口**: `8094`  
**Admin UI Dev 端口**: `8095`（Vite dev server）

```
admin/
  backend/
    main.py        # uvicorn 入口
    requirements.txt
  frontend/        # 独立 Vue3 项目，不依赖 BetterAgent frontend/
  README.md        # 必须包含 "启动 backend" 和 "启动 frontend" 两节
```

### 2.1 人设（Persona）管理

#### 列出所有人设
```
GET http://localhost:8094/api/admin/personas
→ 200
{
  "personas": [
    { "id": "catgirl", "name": "Camelia", "tts_provider": "gpt_sovits", "voice_id": "catgirl_cute" }
  ]
}
```

#### 获取单个人设详情
```
GET http://localhost:8094/api/admin/personas/{persona_id}
→ 200  （完整 YAML 解析为 JSON，保留所有顶级字段）
→ 404  { "error": "not found" }
```

#### 更新人设字段（部分更新）
```
PATCH http://localhost:8094/api/admin/personas/{persona_id}
Content-Type: application/json
```

**允许修改的字段**（白名单，仅此 6 个）:

| 字段 | 类型 |
| :--- | :--- |
| `name` | string |
| `appearance` | string |
| `base_prompt` | string |
| `sleepy_prompt` | string |
| `knowledge_scope` | string |
| `forbidden_topics` | string |

**Response 200**: `{ "status": "ok", "id": "catgirl" }`  
**Response 400**: `{ "error": "Forbidden field: tts" }`（尝试修改白名单外字段时）

> **实现约束**：使用 `ruamel.yaml` 原地更新 YAML，保留注释和字段顺序；禁止用 `yaml.dump` 覆盖整个文件。

### 2.2 用户管理（只读 + 软删除）

```
GET    http://localhost:8094/api/admin/users
GET    http://localhost:8094/api/admin/users/{user_id}
DELETE http://localhost:8094/api/admin/users/{user_id}
```

**GET /users Response 200**:
```json
{
  "users": [
    {
      "user_id": 123456789,
      "display_name": "小明",
      "known_facts": ["喜欢打游戏", "大三学生"],
      "last_seen": "2026-08-17T10:00:00Z"
    }
  ],
  "total": 1
}
```

> **实现约束**：数据来源为 Redis 用户画像 key（`user_profile:{user_id}`）或独立 SQLite 表。
> **绝对禁止**修改 Redis 中的对话历史 key（`short_term:{chat_id}`）。

### 2.3 会话记录查看（只读）

```
GET http://localhost:8094/api/admin/sessions?chat_id={chat_id}&limit=50&offset=0
→ 200
{
  "sessions": [
    { "message_id": 1, "role": "user", "content": "你好喵", "timestamp": 1723456789.0 }
  ],
  "total": 100,
  "chat_id": 123456
}
```

> Redis 不可用时应返回 `{ "sessions": [], "total": 0 }`，不崩溃。

### 2.4 知识库管理（代理，不重复实现逻辑）

Admin 后端作为反向代理，透传至 `campus_kb` 服务：

```
POST http://localhost:8094/api/admin/kb/ingest   →代理→  POST http://localhost:8093/api/kb/ingest
GET  http://localhost:8094/api/admin/kb/search   →代理→  POST http://localhost:8093/api/kb/search
```

> `campus_kb` 服务不可用时，代理接口应返回 `503 { "error": "campus_kb service unavailable" }`。

### 2.5 健康检查

```
GET http://localhost:8094/health
→ 200  { "status": "ok", "service": "admin_backend" }
```

### 2.6 PR 合并门控

- [ ] `GET /health` → `{"status": "ok"}`
- [ ] `GET /api/admin/personas` 列出 `catgirl` 和 `patra`
- [ ] `PATCH /api/admin/personas/catgirl` 修改 `name` 字段成功，YAML 文件实际更新
- [ ] `PATCH /api/admin/personas/catgirl` 传入 `{"tts": {}}` 返回 400
- [ ] `GET /api/admin/sessions?chat_id=0` 在无 Redis 时返回空列表，不崩溃
- [ ] Admin Web UI 在 `localhost:8095` 可访问首页（截图附 PR）
- [ ] PR diff 不包含 `core/`、`shared/`、`runner.py`、`frontend/`、`config/config.yaml` 任何文件的修改

### 2.7 系统配置与 API 密钥管理（BYOK 模式）

> **BYOK（Bring Your Own Key）**：用户无需直接修改仓库根目录 `config/config.yaml`
> 或 `.env`，统一在 Admin 控制台完成 API Key 填写、默认 Provider 切换、连通性测试
> 与配置修改。`.env` 与 `config/config.yaml` 的读写逻辑**仅封装在 `admin/` 内部**，
> 不破坏其它微服务边界。

**管理范围**：`gemini`、`claude`、`qwen` 三个 LLM Provider，对应根 `.env` 的
`GEMINI_API_KEY` / `CLAUDE_API_KEY` / `QWEN_API_KEY` 与 `config/config.yaml` 的
`llm.<provider>.model`。`cosyvoice`（TTS）及 openai/deepseek/ollama/vllm 不在面板管理范围。

#### 读取系统配置（脱敏）

```
GET http://localhost:8094/api/admin/config
```

**Response 200**:
```json
{
  "default_provider": "gemini",
  "network": { "http_proxy": "", "https_proxy": "" },
  "providers": [
    { "name": "gemini", "model": "gemini-2.5-flash", "key_masked": "AIzaSy***4x9", "key_set": true },
    { "name": "claude", "model": "claude-3-5-sonnet-20241022", "key_masked": "sk-ant***xyz", "key_set": true },
    { "name": "qwen", "model": null, "key_masked": null, "key_set": false }
  ]
}
```

| 字段 | 说明 |
| :--- | :--- |
| `default_provider` | 当前默认 LLM Provider（`llm.default_provider`，默认 `gemini`） |
| `network.http_proxy` / `network.https_proxy` | 网络代理（来自 `config/config.yaml`） |
| `providers[].name` | Provider 名（`gemini` / `claude` / `qwen`） |
| `providers[].model` | 该 Provider 当前模型（`llm.<name>.model`，未配置时为 `null`） |
| `providers[].key_masked` | API Key 脱敏（首 6 尾 3，如 `AIzaSy***4x9`）；未设置或占位符为 `null` |
| `providers[].key_set` | 该 Provider 是否已配置有效 API Key |

#### 更新系统配置

```
PATCH http://localhost:8094/api/admin/config
Content-Type: application/json
```

**Request**:
```json
{
  "default_provider": "claude",
  "network": { "http_proxy": "http://127.0.0.1:7890", "https_proxy": "http://127.0.0.1:7890" },
  "providers": {
    "gemini": { "api_key": "AIza...", "model": "gemini-2.5-pro" },
    "claude": { "model": "claude-3-5-sonnet-20241022" }
  }
}
```

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `default_provider` | string（可选） | 切换默认 Provider，必须是 `gemini`/`claude`/`qwen` |
| `network` | object（可选） | `http_proxy` / `https_proxy` 网络代理 |
| `providers` | object（可选） | key 为 Provider 名，value 为 `{ "api_key"?, "model"? }` |

> `api_key` 传空字符串表示**清除**该 Provider 的 key；不传表示不修改。
> `model` 仅在提供时更新。密钥写回仓库根 `.env`；模型 / 默认 Provider / 代理写入
> `config/config.yaml`（ruamel 原地更新，保留注释与字段顺序；文件不存在时从
> `config.yaml.example` 播种）。两者均在 `.gitignore`，不污染 PR diff。

**Response 200**: `{ "status": "ok", "reloaded": true | false }`

> `reloaded` 表示是否成功向 NATS 发布 `agent.config.reloaded`（全服务热刷新信号）。
> 发布失败（NATS 未启动 / 凭据缺失）不阻断配置落盘，仅返回 `false`。

**Response 400**: `{ "error": "Forbidden field: xxx" }`（白名单外字段 / 未知 Provider / 类型错误）

#### 连通性测试

```
POST http://localhost:8094/api/admin/config/test-key
Content-Type: application/json
```

**Request**:
```json
{ "provider": "gemini", "api_key": "AIza..." }
```
`api_key` 可省略：省略时使用 `.env` 中已存 key。**测试不保存任何配置。**

**Response 200（成功）**:
```json
{ "provider": "gemini", "ok": true, "latency_ms": 320, "models": ["gemini-1.5-pro", "gemini-2.5-flash"] }
```

**Response 200（连通失败，HTTP 仍为 200 便于前端展示）**:
```json
{ "provider": "gemini", "ok": false, "latency_ms": 320, "error": "HTTP 400: API key not valid..." }
```

> 探测走各 Provider 公开 models REST 接口：
> - gemini：`GET https://generativelanguage.googleapis.com/v1beta/models?key=...`
> - claude：`GET https://api.anthropic.com/v1/models`（`x-api-key` + `anthropic-version: 2023-06-01`）
> - qwen：DashScope OpenAI-compatible `GET https://dashscope.aliyuncs.com/compatible-mode/v1/models`（`Authorization: Bearer`）
>
> `latency_ms` 为本次探测耗时（毫秒）；`models` 为返回的可用模型列表（截断前 20）。
> 网络代理自动读取 `config/config.yaml` 的 `network.*`。

**Response 400**: `{ "error": "Unknown provider: evil" }`（Provider 不在管理范围内）

#### 热刷新机制

PATCH 成功后，Admin 后端向 NATS 发布 `agent.config.reloaded`，信封格式：

```json
{ "subject": "agent.config.reloaded", "source": "admin_backend", "payload": { "default_provider": "claude", "network": { "...": "..." }, "providers": { "gemini": { "api_key": "..." } } } }
```

> 消费端（cognitive / memory 等服务的 config cache 失效与 Provider 重建）由技术总监在
> 各服务内实现；Admin 只负责**发布**，不订阅。若需触发 Agent 响应，通知技术总监添加
> NATS 订阅点。

### 2.7 PR 合并门控

- [ ] `GET /api/admin/config` 返回 `default_provider` / `network` / `providers`，providers 含 `gemini`/`claude`/`qwen`
- [ ] `PATCH /api/admin/config` 修改 `default_provider` 后 `GET` 能反映，且 `config/config.yaml` 实际更新
- [ ] `PATCH /api/admin/config` 传入未知 Provider（如 `{"providers": {"evil": {}}}`）返回 400
- [ ] `POST /api/admin/config/test-key` 传入未知 Provider 返回 400（不发起外网请求）
- [ ] PR diff 不包含 `core/`、`shared/`、`runner.py`、`frontend/` 任何文件的修改

---

## 接口契约三：陪伴工具服务（张劭哲）

**Feature Branch**: `feat/companion-tools`  
**服务目录**: `services/companion/`  
**固定端口**: `8096`

```
services/companion/
  main.py              # FastAPI 入口
  schedule_service.py  # APScheduler 日程管理
  sql_agent.py         # NL2SQL + SQLite 陪伴数据查询
  recommendation.py    # 规则推荐逻辑
  companion.db         # SQLite 数据库文件（加入 .gitignore）
  requirements.txt
  README.md
```

### 3.0 SQLite Schema（服务启动时自动建表）

```sql
-- 每日对话统计（由技术总监通过 /api/companion/stat 写入）
CREATE TABLE IF NOT EXISTS chat_stats (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id         INTEGER NOT NULL,
  date            TEXT    NOT NULL,        -- 'YYYY-MM-DD'
  msg_count       INTEGER DEFAULT 0,
  proactive_count INTEGER DEFAULT 0
);

-- 情绪历史（同上，技术总监写入）
CREATE TABLE IF NOT EXISTS mood_history (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id     INTEGER NOT NULL,
  ts          REAL    NOT NULL,            -- Unix timestamp
  mood_score  REAL    NOT NULL,           -- -1.0 ~ +1.0
  emotion_tag TEXT    DEFAULT ''
);

-- 话题日志（技术总监写入，张劭哲只读查询）
CREATE TABLE IF NOT EXISTS topic_log (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL,
  ts      REAL    NOT NULL,
  topic   TEXT    NOT NULL,
  source  TEXT    DEFAULT 'user'          -- 'user' | 'agent' | 'game'
);
```

### 3.1 统计写入回调（**由技术总监调用，张劭哲提供接口**）

技术总监每轮 `action_completed` 后 POST 此端点写入陪伴统计数据。

```
POST http://localhost:8096/api/companion/stat
Content-Type: application/json
```
```json
{
  "chat_id": 123456789,
  "date": "2026-08-17",
  "mood_score": 0.72,
  "emotion_tag": "HAPPY",
  "is_proactive": false
}
```
**Response 200**: `{ "status": "ok" }`  
**Response 422**: 字段缺失时返回验证错误，不崩溃。

### 3.2 日程提醒 CRUD

```
POST http://localhost:8096/api/schedule/add
Content-Type: application/json
```
```json
{
  "chat_id": 123456789,
  "user_id": 123456789,
  "title": "高数考试",
  "remind_at": "2026-08-20T09:00:00+08:00",
  "note": "带准考证（可选）"
}
```
**Response 200**: `{ "schedule_id": "uuid-string", "status": "scheduled" }`

```
GET    http://localhost:8096/api/schedule/list?chat_id={chat_id}
→ 200  { "schedules": [ { "schedule_id": "...", "title": "...", "remind_at": "...", "note": "..." } ] }

DELETE http://localhost:8096/api/schedule/{schedule_id}
→ 200  { "status": "deleted" }
→ 404  { "error": "not found" }
```

> **提醒触发机制**（张劭哲用 APScheduler 内部定时，到时调用以下端点）：
>
> 技术总监在 Go Core 侧监听：
> ```
> POST http://localhost:8097/internal/trigger_reminder        ← 技术总监内部端口
> { "chat_id": 123456789, "title": "高数考试", "note": "带准考证" }
> ```
> 张劭哲只需在 APScheduler 任务里 POST 这个地址，技术总监收到后转 NATS `agent.inbound_message`。  
> **张劭哲不直接操作 NATS，不 import 任何 NATS 库。**

### 3.3 SQL Agent 陪伴数据查询

```
POST http://localhost:8096/api/companion/query
Content-Type: application/json
```
```json
{
  "chat_id": 123456789,
  "natural_language_query": "我们这周聊了多少次？"
}
```
**Response 200**:
```json
{
  "answer": "这周你一共和我聊了 17 次喵～",
  "sql_executed": "SELECT SUM(msg_count) FROM chat_stats WHERE chat_id=123456789 AND date >= '2026-08-11'",
  "raw_result": [{ "sum(msg_count)": 17 }]
}
```

> 可用 LLM（Gemini）或规则模板实现 NL2SQL，查询范围限定在  
> `chat_stats`、`mood_history`、`topic_log` 三张表。  
> **SQL 白名单**：只允许执行 `SELECT`，其余操作一律返回 `400 { "error": "Only SELECT is allowed" }`。

### 3.4 任务推荐

```
GET http://localhost:8096/api/companion/recommendations?chat_id={chat_id}
```
**Response 200**:
```json
{
  "recommendations": [
    "今天还没聊过学习话题哦，要一起复习吗喵？",
    "高数考试还有 3 天，要不要定一个复习提醒？"
  ]
}
```

> 推荐逻辑（纯规则，**不需要 LLM**）：
> 1. 查今日 `topic_log` → 若缺少 "study" 话题则推荐学习类提示
> 2. 查未来 24 小时内的 `schedule` 项 → 生成"快到期提醒"文案
> 3. 模板字符串拼接，无数据时返回 `{ "recommendations": [] }`，不崩溃

### 3.5 健康检查

```
GET http://localhost:8096/health
→ 200  { "status": "ok", "service": "companion" }
```

### 3.6 PR 合并门控

- [ ] `GET /health` → `{"status": "ok"}`
- [ ] `POST /api/companion/stat` 写入一条统计，返回 200
- [ ] `POST /api/schedule/add` 返回合法 UUID `schedule_id`
- [ ] `GET /api/schedule/list` 命中刚创建的记录
- [ ] `DELETE /api/schedule/{id}` 删除后再 GET 该记录消失
- [ ] `POST /api/companion/query` 用自然语言查询返回合法 JSON（`answer` 非空）
- [ ] `GET /api/companion/recommendations` 有无数据均不崩溃
- [ ] `companion.db` 路径在 `.gitignore` 中（不提交二进制文件）
- [ ] PR diff 不包含 `core/`、`shared/`、`runner.py`、`frontend/` 任何文件的修改

---

## 端口分配汇总

| 端口 | 服务 | 负责人 | 状态 |
| :--- | :--- | :--- | :--- |
| `4222` | NATS Server | 褚裕禄 | ✅ 已有 |
| `8080` | Go Core WebGateway (WebSocket) | 褚裕禄 | ✅ 已有 |
| `8090` | Go Core 游戏事件 HTTP 摄入 | 褚裕禄 | ✅ 已有 |
| `8091` | TTS Service | 褚裕禄 | ✅ 已有 |
| `8092` | STT Service | 褚裕禄 | ✅ 已有 |
| `8093` | **Campus KB RAG Service** | 冯文哲 | 🔲 待实现 |
| `8094` | **Admin Backend REST API** | 谢自立 | 🔲 待实现 |
| `8095` | **Admin Web UI (Vite Dev)** | 谢自立 | 🔲 待实现 |
| `8096` | **Companion Tool Service** | 张劭哲 | 🔲 待实现 |

---

## Git 工作流规范

### 分支命名

```
feat/admin-panel        ← 谢自立
feat/campus-kb          ← 冯文哲
feat/companion-tools    ← 张劭哲
feat/go-core-*          ← 褚裕禄（按功能细分）
```

### 提交消息（Conventional Commits 格式）

```
feat(admin): add persona PATCH endpoint
fix(campus-kb): handle empty qdrant result
test(admin): add persona forbidden field test
```

### PR 通用硬性门控（所有 PR 必须满足）

1. `pytest tests/test_api_contract.py -k <your_module>` 全部 PASS
2. PR diff 不包含受保护路径（见下方）
3. `GET /health` 正常
4. `services/<your_service>/README.md` 存在且含启动命令
5. 代码中无硬编码密钥，所有敏感值从 `.env` 读取

### 受保护路径（Merge 前硬性检查，技术总监手动核查 diff）

```
core/
shared/
runner.py
frontend/
config/config.yaml
```

---

## 集成测试脚本（由褚裕禄维护）

**路径**: `tests/test_api_contract.py`

```python
"""
API Contract Integration Tests
运行方式: pytest tests/test_api_contract.py -v
前提: 各服务已在对应端口启动
"""
import pytest
import requests

KB_BASE    = "http://localhost:8093"
ADMIN_BASE = "http://localhost:8094"
COMP_BASE  = "http://localhost:8096"


class TestCampusKB:
    def test_health(self):
        r = requests.get(f"{KB_BASE}/health", timeout=5)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_ingest(self):
        docs = [
            {"content": "图书馆周一至周五开放至22:00，周末20:00关闭。",
             "source": "test_lib.md", "category": "faq", "metadata": {}},
            {"content": "校内超市位于第三食堂一楼，营业时间07:00-23:00。",
             "source": "test_shop.md", "category": "service", "metadata": {}},
            {"content": "选课系统每学期第9周开放，登录教务处网站操作。",
             "source": "test_schedule.md", "category": "schedule", "metadata": {}},
        ]
        r = requests.post(f"{KB_BASE}/api/kb/ingest", json={"documents": docs}, timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body["ingested"] == 3
        assert body["failed"] == 0

    def test_search_returns_result(self):
        r = requests.post(f"{KB_BASE}/api/kb/search",
                          json={"query": "图书馆几点关门", "top_k": 3}, timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 1
        assert "content" in body["results"][0]
        assert "score" in body["results"][0]

    def test_search_empty_query_graceful(self):
        r = requests.post(f"{KB_BASE}/api/kb/search",
                          json={"query": "xyzzy不存在的内容abc", "top_k": 3}, timeout=10)
        assert r.status_code == 200
        # 不崩溃即可，total 可为 0


class TestAdminPanel:
    def test_health(self):
        r = requests.get(f"{ADMIN_BASE}/health", timeout=5)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_list_personas(self):
        r = requests.get(f"{ADMIN_BASE}/api/admin/personas", timeout=5)
        assert r.status_code == 200
        ids = [p["id"] for p in r.json()["personas"]]
        assert "catgirl" in ids
        assert "patra" in ids

    def test_get_persona_detail(self):
        r = requests.get(f"{ADMIN_BASE}/api/admin/personas/catgirl", timeout=5)
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "catgirl"
        assert "name" in body
        assert "base_prompt" in body

    def test_patch_persona_allowed_field(self):
        # 备份原始值
        original = requests.get(f"{ADMIN_BASE}/api/admin/personas/catgirl",
                                timeout=5).json()["name"]
        r = requests.patch(f"{ADMIN_BASE}/api/admin/personas/catgirl",
                           json={"name": "__contract_test__"}, timeout=5)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        # 验证实际写入
        updated = requests.get(f"{ADMIN_BASE}/api/admin/personas/catgirl",
                               timeout=5).json()["name"]
        assert updated == "__contract_test__"
        # 恢复
        requests.patch(f"{ADMIN_BASE}/api/admin/personas/catgirl",
                       json={"name": original}, timeout=5)

    def test_patch_persona_forbidden_field(self):
        r = requests.patch(f"{ADMIN_BASE}/api/admin/personas/catgirl",
                           json={"tts": {"provider": "evil"}}, timeout=5)
        assert r.status_code == 400
        assert "error" in r.json()

    def test_patch_persona_forbidden_id(self):
        r = requests.patch(f"{ADMIN_BASE}/api/admin/personas/catgirl",
                           json={"id": "hacked"}, timeout=5)
        assert r.status_code == 400

    def test_sessions_graceful_no_redis(self):
        r = requests.get(f"{ADMIN_BASE}/api/admin/sessions?chat_id=0&limit=10", timeout=5)
        assert r.status_code == 200
        body = r.json()
        assert "sessions" in body
        assert isinstance(body["sessions"], list)


class TestCompanionTools:
    def test_health(self):
        r = requests.get(f"{COMP_BASE}/health", timeout=5)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_schedule_create(self):
        payload = {
            "chat_id": 999, "user_id": 999,
            "title": "契约测试提醒",
            "remind_at": "2099-12-31T09:00:00+08:00",
            "note": "自动化测试创建"
        }
        r = requests.post(f"{COMP_BASE}/api/schedule/add", json=payload, timeout=5)
        assert r.status_code == 200
        body = r.json()
        assert "schedule_id" in body
        assert body["status"] == "scheduled"
        return body["schedule_id"]

    def test_schedule_list(self):
        sid = self.test_schedule_create()
        r = requests.get(f"{COMP_BASE}/api/schedule/list?chat_id=999", timeout=5)
        assert r.status_code == 200
        ids = [s["schedule_id"] for s in r.json().get("schedules", [])]
        assert sid in ids

    def test_schedule_delete(self):
        sid = self.test_schedule_create()
        r = requests.delete(f"{COMP_BASE}/api/schedule/{sid}", timeout=5)
        assert r.status_code == 200
        # 验证已删除
        r2 = requests.get(f"{COMP_BASE}/api/schedule/list?chat_id=999", timeout=5)
        ids = [s["schedule_id"] for s in r2.json().get("schedules", [])]
        assert sid not in ids
```

---

## 变更流程

1. 对本文档的任何修改，须由**技术总监**在 PR 描述中标注 `[contract-change]`。
2. 组员**不得自行修改**本文档。
3. 破坏性接口变更（如字段重命名、删除、类型变更）需提前至少 **1 天**在群里通知，确认无冲突后方可合并。
4. 接口现处于 `v1`，破坏性变更时升版到 `v2`（路径前缀变为 `/api/v2/...`）。

---

*最后更新：2026-08-21 | 维护者：褚裕禄*
