# BetterAgent (虚拟数字人陪伴系统)

> **全双工多模态数字人陪伴系统 (Full-Duplex Digital Human Companion System)**  
> 采用 **Go Core (高性能控制核) + NATS (中枢消息总线) + Python Services (认知/记忆微服务) + Vue 3 / Better Agent (Live2D/VRM 数字人前端)** 的异构微服务架构。

---

## 🌟 核心特性 (Key Features)

- **🎭 多模态数字人交互**：支持基于 WebSockets (`:8080`) 的全双工音视频流传输、实时 PCM 音频切片播放与 Live2D 口型（Viseme Lip-sync）高精度驱动。
- **⚡ 毫秒级打断 (Barge-in)**：支持用户中途打断与语音重推，基于原子 `generation_id` 代际防护机制自动清空在途过期的音频切片与口型帧。
- **🧠 异步并发状态机**：Go 实现的 `CentralStateMachine`，拥有 45 秒 Deadman Switch Watchdog 超时自愈、2小时空闲自动 Evict 回收与多维度会话隔离。
- **🧬 心理与生理计算引擎**：内建 3D VAD 情感模型（Valence/Arousal/Dominance）、生理指标（Energy/SocialBattery/Affection）、`CircadianRhythm` 昼夜生物钟与 `UrgeEngine` 枯燥度主动开口触发器。
- **🎮 游戏自主感知与打牌解说**：集成杀戮尖塔 2 (Slay the Spire 2) C# Mod，实现游戏事件摄入 (`:8090`)、自动出牌决策与实时解说 HUD 叠加。
- **📚 团队多微服务隔离与契约**：基于 [API-CONTRACT.md](docs/API-CONTRACT.md) 协议隔离，外包/组员独立承接校园 FAQ 知识库 (RAG `:8093`)、B 端后台管理控制台 (`:8094`/`:8095`) 与 SQLite 陪伴工具 (`:8096`)。

---

## 🏗️ 系统架构拓扑 (Architecture Topology)

```
                       ┌─────────────────────────────────────────┐
                       │  Web Client (stage-web / Live2D / VRM)  │
                       └───────────────────┬─────────────────────┘
                                           │ WebSocket (端口 8080)
┌──────────────────────────────────────────▼─────────────────────────────────────────┐
│ Go Core (betteragent-core)                                                        │
│ ├── WebGateway (全双工 WebSocket 网关)      ├── CentralStateMachine (状态机看门狗)    │
│ ├── GotdAdapter (Telegram MTProto)        ├── EmotionEngine & UrgeEngine (心理模型) │
│ └── GameEventHandler / GameStateHandler   └── Go NatsBus (消息发布订阅)            │
└──────────────────────────────────────────┬─────────────────────────────────────────┘
                                           │ NATS Server (Pub/Sub 端口 4222)
┌──────────────────────────────────────────▼─────────────────────────────────────────┐
│ Python Services Layer                                                             │
│ ├── Cognitive Service (:8091 TTS / :8092 STT / Gemini / Claude / Tools / MCP)     │
│ ├── Memory Service (Redis 短时缓存 + Qdrant 向量检索 + UserProfile 用户画像)          │
│ └── Game Watcher Service (STS2 轮询与自动出牌触发)                                │
└──────────────────────────────────────────┬─────────────────────────────────────────┘
                                           │ HTTP REST / API Contract
┌──────────────────────────────────────────▼─────────────────────────────────────────┐
│ Team Isolated Subservices                                                         │
│ ├── Campus KB RAG Service (冯文哲 / 端口 8093)                                     │
│ ├── Admin Panel Backend & Web UI (谢自立 / REST 端口 8094, Web 端口 8095)           │
│ └── Companion Tool Service (张劭哲 / 端口 8096, SQLite companion.db)                │
└───────────────────────────────────────────────────────────────────────────────────┘
```

---

## 📚 项目文档 (Documentation)

- 📐 [系统架构与 SRS 规范](docs/ARCHITECTURE.md) —— 系统总体拓扑、WebGateway 双工时序图、UML 类图与状态机迁移矩阵。
- 🤝 [团队多微服务接口契约](docs/API-CONTRACT.md) —— 各 Feature 分支的端口隔离规范、HTTP REST 契约与 PR 门控检查。
- 🛡️ [安全加固记录与部署基线](docs/SECURITY.md) —— 鉴权环境变量与生产环境信任边界清单。
- 📜 [变更日志 (CHANGELOG.md)](CHANGELOG.md) —— 按语义化版本与 Commit 规范记录的项目迭代日志。

---

## 🔌 端口分配汇总 (Port Assignments)

| 端口 | 服务名称 | 协议 | 负责人 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| `4222` | NATS Server | TCP Pub/Sub | 核心 (褚裕禄) | ✅ 已就绪 |
| `8080` | Go Core WebGateway | WebSocket | 核心 (褚裕禄) | ✅ 已就绪 |
| `8090` | Go Core 游戏事件摄入 | HTTP / WS | 核心 (褚裕禄) | ✅ 已就绪 |
| `8091` | TTS Service | HTTP | 核心 (褚裕禄) | ✅ 已就绪 |
| `8092` | STT Service | HTTP | 核心 (褚裕禄) | ✅ 已就绪 |
| `8093` | **Campus KB RAG Service** | HTTP REST | 冯文哲 | ✅ 已就绪 |
| `8094` | **Admin Panel REST API** | HTTP REST | 谢自立 | ✅ 已就绪 |
| `8095` | **Admin Web UI (Vite)** | HTTP Dev | 谢自立 | ✅ 已就绪 |
| `8096` | **Companion Tool Service** | HTTP REST | 张劭哲 | ✅ 已就绪 |

<details>
<summary>各子服务已实现接口摘要</summary>

**Campus KB (`:8093`, `services/campus_kb/`)**
- `GET  /health` — 健康检查
- `POST /api/kb/ingest` — 知识条目向量入库
- `POST /api/kb/search` — 语义相似度检索

**Admin Panel REST (`:8094`, `admin/backend/`)**
- `GET/POST/PATCH/DELETE /api/admin/personas/{id}` — 人设 YAML 全生命周期管理（ruamel 圆形读写，保留注释与格式）
- `GET/DELETE /api/admin/users/{user_id}` — 用户管理（只读 + 软删除）
- `GET /api/admin/sessions`, `GET /api/admin/sessions/overview` — 会话记录查看（Redis 短时历史）
- `GET/DELETE /api/admin/memory/short-term` — 短期记忆管理
- `GET/DELETE /api/admin/memory/long-term/{point_id}` — 长期记忆（Qdrant）管理
- `GET/PUT /api/admin/memory/profile/{user_id}` — 用户事实画像管理
- `GET/POST /api/admin/kb/*` — 校园知识库代理（透传至 `:8093`）
- `GET/POST/DELETE /api/admin/schedules` — 日程提醒管理（代理至 `:8096`）
- `GET/PATCH /api/admin/config` — 系统配置在线查看与修改

**Admin Web UI (`:8095`, `admin/frontend/`)**
- Vue 3 + Element Plus 独立后台管理界面，`dist/` 已构建完成

**Companion Service (`:8096`, `services/companion/`)**
- `POST /api/companion/stat` — 陪伴统计写入
- `POST/GET/DELETE /api/schedule/*` — 日程 CRUD（APScheduler 到期触发 NATS 推送）
- `POST /api/companion/query` — NL2SQL 陪伴数据自然语言查询
- `GET  /api/companion/recommendations` — 任务推荐（规则引擎）
- `GET/POST/DELETE /api/user_profile/*` — 用户事实画像管理
- `GET  /api/memory/stats` — 记忆统计

</details>

---

## 🚀 快速启动指南 (Quick Start)

### 1. 环境准备 (Python 3.10+)

```bash
# 创建并激活 Python 虚拟环境
python -m venv .venv
.\.venv\Scripts\activate      # Windows
source .venv/bin/activate    # Linux/macOS

# 安装依赖
pip install -r requirements.txt
```

### 2. 环境变量配置

```bash
# 复制配置模板
cp .env.example .env

# 编辑 .env 填充秘钥（必须包含 NATS_USER/NATS_PASSWORD/WEBGATEWAY_TOKEN/REDIS_PASSWORD）
```

### 3. 一键拉起后端微服务矩阵

使用内置的进程守护编排脚本 `runner.py`（支持自动拉起 Go Core、NATS、Cognitive、Memory 与 TTS 微服务）：

```bash
python runner.py
```

### 4. 启动前端数字人 Web UI

```bash
# 进入前端目录
cd frontend

# 安装 pnpm 依赖
pnpm install

# 启动 stage-web 调试端 (端口 5173)
pnpm --filter @proj-airi/stage-web dev
```

在浏览器打开 `http://localhost:5173` 即可与数字人猫娘进行音视频全双工交互！

### 5. 运行团队接口契约集成测试

在组员提交 PR 或集成联调前，运行 API 门控测试：

```bash
pytest tests/test_api_contract.py -v
```

---

## 📄 许可与鸣谢 (Acknowledgments)

- 基于 **Airi 项目** 的前端数字人与通信框架：[moeru-ai/airi](https://github.com/moeru-ai/airi)
- 采用 **gotd/td** 高性能 Go Telegram MTProto 库：[gotd/td](https://github.com/gotd/td)