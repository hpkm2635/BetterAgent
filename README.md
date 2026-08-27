<picture>
  <source
    width="100%"
    srcset="./docs/images/banner-dark.svg"
    media="(prefers-color-scheme: dark)"
  />
  <source
    width="100%"
    srcset="./docs/images/banner-light.svg"
    media="(prefers-color-scheme: light), (prefers-color-scheme: no-preference)"
  />
  <img width="100%" src="./docs/images/banner-dark.svg" alt="BetterAgent Banner" />
</picture>

<h1 align="center">BetterAgent</h1>

<p align="center">全双工多模态数字人陪伴系统 · Full-Duplex Digital Human Companion System</p>

<p align="center">
  <a href="https://github.com/hpkm2635/BetterAgent/blob/main/LICENSE"><img src="https://img.shields.io/github/license/hpkm2635/BetterAgent?style=flat&colorA=080f12&colorB=7c3aed" alt="License" /></a>
  <img src="https://img.shields.io/badge/Go-1.22+-00ADD8?style=flat&logo=go&logoColor=white&labelColor=080f12" alt="Go" />
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB?style=flat&logo=python&logoColor=white&labelColor=080f12" alt="Python" />
  <img src="https://img.shields.io/badge/Vue_3-4FC08D?style=flat&logo=vuedotjs&logoColor=white&labelColor=080f12" alt="Vue 3" />
  <img src="https://img.shields.io/badge/NATS-27AAE1?style=flat&logo=natsdotio&logoColor=white&labelColor=080f12" alt="NATS" />
  <img src="https://img.shields.io/badge/Telegram-26A5E4?style=flat&logo=telegram&logoColor=white&labelColor=080f12" alt="Telegram" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Live2D-支持-ff6b9d?style=flat&labelColor=080f12" alt="Live2D" />
  <img src="https://img.shields.io/badge/VRM-支持-9b59b6?style=flat&labelColor=080f12" alt="VRM" />
  <img src="https://img.shields.io/badge/STS2_Mod-游戏解说-e67e22?style=flat&labelColor=080f12" alt="STS2" />
  <img src="https://img.shields.io/badge/EmotionEngine-3D_VAD-1abc9c?style=flat&labelColor=080f12" alt="EmotionEngine" />
</p>

---

> 深受 [Project AIRI](https://github.com/moeru-ai/airi) 启发，致力于构建开放、可本地部署、能与用户双向共情的数字人陪伴系统。

> [!TIP]
> **一键拉起所有微服务**（NATS · Go Core · Cognitive · Memory · TTS · STT · Campus KB · Admin · Companion · Game Watcher）：
>
> ```bash
> python runner.py
> ```
>
> 没有 Python/Node/Go 开发环境的机器（比如给同学演示用的电脑），可以直接用打包好的**便携版**，解压即用，见下方[快速启动](#快速启动)。

> [!WARNING]
> 本项目不与任何加密货币或 NFT 项目关联，请注意甄别相关虚假信息。

> [!NOTE]
> 系统架构、状态机迁移矩阵、NATS Payload Schema 等完整规范见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。
> 多微服务团队接口契约（端口隔离 · HTTP 契约 · PR 门控）见 [`docs/API-CONTRACT.md`](docs/API-CONTRACT.md)。

---

## 这是什么

BetterAgent 是一个课程项目：给一位数字人搭一整套"能听、能说、能记、能主动开口"的后端，而不是一个套壳 LLM 聊天框。具体来说：

- **不是纯文本聊天前端** —— 语音输入输出走的是真正的全双工音频流（浏览器麦克风 → WebSocket 二进制帧 → NATS → STT/TTS），支持毫秒级打断（barge-in），不是"录一句、等一句"的轮流对话。
- **不是靠 Prompt 硬演的人设** —— 情绪状态（Valence / Arousal / Dominance 三维模型）是每轮对话真实计算出来的数值，会随生物钟衰减、随互动积累，注入到 System Prompt 里的是这一刻的真实状态，不是写死的性格描述。
- **不是只会被动应答** —— UrgeEngine 会在用户长时间不说话、游戏里发生大事件（死亡/胜利）、日程提醒到点时，主动发起一轮对话，而不是等你先开口。
- **是一个真实的多微服务系统**，不是单体脚本：Go 负责实时链路（WebSocket 网关、状态机、Telegram MTProto 客户端），Python 负责认知/记忆/校园知识库/陪伴功能等业务逻辑，NATS 把它们串起来，工程上是按"能横向扩展、能容错重启"的标准写的，不是能跑就行。
- **还能感知游戏**——接入杀戮尖塔 2 的 C# Mod，实时读取战斗状态，能自主出牌也能实时解说；长期记忆走 Redis 短时缓冲 + Qdrant 向量检索，聊过的事情不会说完就忘。

## 画面预览

<!--
  实机截图占位 —— 把截图放进 docs/images/screenshots/ 目录，文件名对应下方三张图即可自动显示：
    - stage-web-chat.png   数字人对话界面（Live2D/VRM + 实时字幕 + 打字机效果）
    - live2d-model.png     Live2D 立绘 / 表情与口型同步
    - admin-panel.png      后台管理面板（人设编辑 / 会话记录 / 系统配置）
-->
<p align="center">
  <img src="./docs/images/screenshots/stage-web-chat.png" width="90%" alt="数字人对话界面" />
</p>
<p align="center">
  <img src="./docs/images/screenshots/live2d-model.png" width="44%" alt="Live2D 立绘与口型同步" />
  <img src="./docs/images/screenshots/admin-panel.png" width="44%" alt="后台管理面板" />
</p>

---

## 核心能力

### 🧠 认知与记忆

- [x] 多 LLM 提供商支持（Gemini · Claude · OpenAI-compatible），后台面板在线切换 + BYOK API Key 管理
- [x] 结构化 System Prompt 注入（情绪状态 · 生物钟 · 用户画像）
- [x] MCP 工具注册与调用（ToolRegistry）
- [x] Redis 短期记忆缓冲（ShortTermBuffer，不可用时自动降级为纯内存态）
- [x] Qdrant 向量长期记忆（VectorMemoryStore）
- [x] 用户事实画像管理（UserProfileManager）
- [x] Ebbinghaus 记忆衰减整合（MemoryConsolidator）
- [x] TokenBudget 上下文裁剪（TokenBudgetManager）
- [x] 校园知识库 RAG 集成，回复自动标注引用来源
- [x] 人设（含 TTS 语音配置）后台在线编辑 + 热更新，无需重启

### 🫀 情绪与生理引擎

- [x] 3D 情感模型（Valence / Arousal / Dominance）
- [x] 生理指标（Energy · SocialBattery · Affection）
- [x] CircadianRhythm 昼夜生物钟衰减
- [x] UrgeEngine 枯燥度 / 游戏事件 / 日程到点触发主动开口
- [x] CentralStateMachine 45 秒 Deadman Switch 自愈看门狗
- [x] 2 小时空闲自动 Evict 会话回收

### 👂 听觉与打断

- [x] 全双工 WebSocket 音频流（Go WebGateway · 端口 8080）
- [x] 毫秒级 Barge-in 打断，基于 `generation_id` 原子代际防护
- [x] 过期音频切片与口型帧自动清空
- [x] STT 语音识别（FunASR / iFlytek 可选，NATS 内部通信，无独立端口）

### 🗣️ 语音合成

- [x] GPT-SoVITS 本地高质量 TTS（外部进程，NATS 内部通信，无独立端口）
- [x] CosyVoice 云端 TTS 备选
- [x] Viseme 时间轴驱动 Live2D 口型（`ParamMouthOpenY`），无 Viseme 数据时自动回退到实时频谱分析口型

### 🎭 数字人前端

- [x] Live2D 模型支持（口型 · 眼神 · 自动眨眼）
- [x] VRM 模型支持（口型 · 眼神 · 自动眨眼）
- [x] Neuro-sama 式打字机字幕，逐块揭示节奏对齐真实播放进度
- [x] Vue 3 + Pinia + VueUse + UnoCSS（基于 AIRI stage-web 框架）

### 🎮 游戏自主感知（杀戮尖塔 2）

- [x] STS2 C# Mod 接入（游戏事件摄入 · 端口 8090）
- [x] 实时游戏状态解析与自动出牌决策
- [x] Game Watcher Service 轮询与触发
- [x] 解说 HUD 叠加

### 🤝 多渠道对话

- [x] Go gotd MTProto 高性能 Telegram 客户端
- [x] Web 前端（stage-web）全双工对话，与 Telegram 共享同一套记忆/人设/情绪引擎
- [x] 多聊天会话状态隔离（Telegram / Web 独立命名空间，互不干扰）
- [x] 人性化延迟与反 Spam 机制

### ⚙️ 后台管理

- [x] 人设全生命周期管理（新增 / 编辑 / 软删除 / 一键设为当前活跃人设）
- [x] 会话记录浏览（自动发现所有活跃会话，Telegram / Web 渠道标注，无需手动输入 chat_id）
- [x] 用户画像与日程提醒管理
- [x] 校园知识库检索测试
- [x] 系统配置与 API 密钥在线管理（BYOK）
- [x] 共享密钥登录门（`ADMIN_SECRET_KEY`），适配便携包等无 Docker 环境

---

## 系统架构

```mermaid
%%{ init: { 'flowchart': { 'curve': 'catmullRom' }, 'theme': 'dark' } }%%

flowchart TD
  Client("🌐 Web Client\nstage-web / Live2D / VRM")
  Telegram("📱 Telegram\ngotd MTProto")

  subgraph GoCore["⚡ Go Core (betteragent-core)"]
    WebGW("WebGateway\n全双工 WebSocket :8080")
    GotdAdapter("GotdAdapter\nTelegram MTProto")
    CSM("CentralStateMachine\nDeadman Switch Watchdog")
    EmotionEng("EmotionEngine\nVAD · CircadianRhythm")
    NatsBus("Go NatsBus\nPub/Sub")
    GameHandler("GameEventHandler\n:8090")
  end

  subgraph PythonLayer["🐍 Python Services Layer"]
    CogSvc("Cognitive Service\nLLM · PromptBuilder · ToolRegistry")
    MemSvc("Memory Service\nRedis · Qdrant · UserProfile")
    TTSSvc("TTS Service\nGPT-SoVITS · CosyVoice")
    STTSvc("STT Service\nFunASR · iFlytek")
    GameWatcher("Game Watcher\nSTS2 轮询触发")
  end

  subgraph TeamServices["👥 团队隔离子服务"]
    CampusKB("Campus KB RAG\n:8093")
    AdminAPI("Admin Panel REST\n:8094")
    AdminUI("Admin Web UI\n:8095")
    CompanionSvc("Companion Tool\n:8096")
  end

  Client -->|WebSocket| WebGW
  Telegram -->|MTProto| GotdAdapter
  WebGW --> CSM
  GotdAdapter --> CSM
  CSM --> EmotionEng
  CSM --> NatsBus
  GameHandler --> NatsBus

  NatsBus -->|agent.cognitive.*| CogSvc
  NatsBus -->|agent.memory.*| MemSvc
  NatsBus -->|agent.tts.*| TTSSvc
  NatsBus -->|agent.stt.*| STTSvc
  NatsBus -->|agent.game.*| GameWatcher
  NatsBus -->|agent.schedule.fired| WebGW

  CogSvc -->|HTTP REST| CampusKB
  CogSvc -->|HTTP REST| CompanionSvc
  AdminAPI -->|代理| CampusKB
  AdminAPI -->|代理| CompanionSvc

  style GoCore fill:#1a1a2e,stroke:#7c3aed,stroke-width:2px,color:#e2e8f0
  style PythonLayer fill:#0f2027,stroke:#0891b2,stroke-width:2px,color:#e2e8f0
  style TeamServices fill:#1a1200,stroke:#d97706,stroke-width:2px,color:#e2e8f0
  style WebGW fill:#2d1b69,stroke:#7c3aed,stroke-width:1px,color:#c4b5fd
  style CSM fill:#2d1b69,stroke:#7c3aed,stroke-width:1px,color:#c4b5fd
  style EmotionEng fill:#2d1b69,stroke:#7c3aed,stroke-width:1px,color:#c4b5fd
  style NatsBus fill:#2d1b69,stroke:#7c3aed,stroke-width:1px,color:#c4b5fd
  style CogSvc fill:#0c2340,stroke:#0891b2,stroke-width:1px,color:#7dd3fc
  style MemSvc fill:#0c2340,stroke:#0891b2,stroke-width:1px,color:#7dd3fc
  style TTSSvc fill:#0c2340,stroke:#0891b2,stroke-width:1px,color:#7dd3fc
  style STTSvc fill:#0c2340,stroke:#0891b2,stroke-width:1px,color:#7dd3fc
```

---

## 微服务端口分配

| 端口 | 服务 | 协议 | 状态 |
| :--: | :--- | :--- | :---: |
| `4222` | NATS Server | TCP Pub/Sub | ✅ |
| `8080` | Go Core WebGateway | WebSocket | ✅ |
| `8090` | 游戏事件摄入 | HTTP / WS | ✅ |
| `8093` | Campus KB RAG | HTTP REST | ✅ |
| `8094` | Admin Panel REST API | HTTP REST | ✅ |
| `8095` | Admin Web UI | HTTP | ✅ |
| `8096` | Companion Tool Service | HTTP REST | ✅ |

> TTS / STT / Cognitive / Memory / Game Watcher 均为纯 NATS 内部服务，不监听任何 HTTP/WS 端口——它们只通过消息总线与其它服务通信，不对外暴露接口。

<details>
<summary>各子服务已实现接口摘要</summary>

**Campus KB (`:8093`, `services/campus_kb/`)**
- `GET  /health` — 健康检查
- `POST /api/kb/ingest` — 知识条目向量入库
- `POST /api/kb/search` — 语义相似度检索

**Admin Panel REST (`:8094`, `admin/backend/`)**
- `GET/POST/PATCH/DELETE /api/admin/personas/{id}` — 人设 YAML 全生命周期管理（含 `tts` 语音配置子字段热更新）
- `POST /api/admin/personas/{id}/activate` — 一键切换当前系统活跃人设
- `GET/DELETE /api/admin/users/{user_id}` — 用户管理（只读 + 软删除）
- `GET /api/admin/sessions`, `GET /api/admin/sessions/overview` — 会话记录查看，自动发现 Telegram / Web 全部活跃会话
- `GET/DELETE /api/admin/memory/short-term` — 短期记忆管理
- `GET/DELETE /api/admin/memory/long-term/{point_id}` — 长期记忆（Qdrant）管理
- `GET/PUT /api/admin/memory/profile/{user_id}` — 用户事实画像管理
- `GET/POST /api/admin/kb/*` — 校园知识库代理（透传至 `:8093`）
- `GET/POST/DELETE /api/admin/schedules` — 日程提醒管理
- `GET/PATCH /api/admin/config` — 系统配置在线查看与修改（BYOK API Key）

**Admin Web UI (`:8095`, `admin/frontend/`)**
- Vue 3 独立后台管理界面，共享密钥登录门（`ADMIN_SECRET_KEY`）

**Companion Service (`:8096`, `services/companion/`)**
- `POST /api/companion/stat` — 陪伴统计写入
- `POST/GET/DELETE /api/schedule/*` — 日程 CRUD（APScheduler 到期触发，通过 `agent.schedule.fired` 交给 UrgeEngine 生成主动提醒消息）
- `POST /api/companion/query` — NL2SQL 陪伴数据自然语言查询
- `GET  /api/companion/recommendations` — 任务推荐（规则引擎）
- `GET/POST/DELETE /api/user_profile/*` — 用户事实画像管理
- `GET  /api/memory/stats` — 记忆统计

</details>

---

## 快速启动

### 方式一：便携版（推荐给没有开发环境的机器）

打包好的便携版内置 Go 二进制、NATS、Qdrant 与预装好依赖的便携 Python 运行时，目标机器不需要装 Python / Node.js / Go / Docker，解压即用。构建产物是 Windows 专用，需要在 Windows 上跑一遍构建脚本：

```powershell
python scripts\build_portable_package.py
```

产出 `portable_package/` 目录，拷给对方后配置好 `.env`（复制 `.env.example` 并填入密钥），双击 `START.bat` 即可。完整说明、覆盖范围与已知限制见 [`docs/PORTABLE_PACKAGE.md`](docs/PORTABLE_PACKAGE.md)。

### 方式二：源码开发

#### 1. 环境准备

```bash
# Python 3.12+，创建并激活虚拟环境
python -m venv .venv
.\.venv\Scripts\activate      # Windows
source .venv/bin/activate     # Linux / macOS

pip install -r requirements.txt
```

#### 2. 环境变量配置

```bash
cp .env.example .env
# 填充 NATS_USER / NATS_PASSWORD / WEBGATEWAY_TOKEN / REDIS_PASSWORD 等必要密钥
```

#### 3. 一键拉起后端微服务矩阵

```bash
python runner.py
```

`runner.py` 自动编排并守护以下进程：NATS Server · Go Core · Cognitive · Memory · TTS · STT · Campus KB · Admin 后端/前端 · Companion · Game Watcher。

#### 4. 启动前端数字人 Web UI

```bash
cd frontend
pnpm install
pnpm --filter @proj-airi/stage-web dev
# → 浏览器打开 http://localhost:5173
```

#### 5. 运行测试

```bash
# 团队 API 契约门控测试（在组员提交 PR 或集成联调前运行，需要对应服务已启动）
pytest tests/test_api_contract.py -v

# 全量单元测试
pytest tests/ -v
go test -race ./...   # core/ 目录下
```

---

## 项目状态

课程项目开发中，按下面 5 个方向拆分推进，均已具备可演示的核心链路：

| 方向 | 状态 | 说明 |
| :--- | :--: | :--- |
| 人设设计 | ✅ | YAML 驱动人设，后台在线编辑 + 热更新（文本字段与 TTS 语音配置均支持，无需重启） |
| 校园知识库 | ✅ | RAG 检索 + 回复自动标注引用来源，与语音/字幕管线解耦 |
| 陪伴 Agent | ✅ | 日程提醒、用户画像、任务推荐；日程到点由 UrgeEngine 生成主动消息，而非固定模板 |
| 用户前端 | ✅ | 全双工语音、Live2D/VRM、打字机字幕、Viseme 口型同步 |
| 后台管理 | ✅ | 人设/用户/会话/日程/知识库/系统配置全覆盖，共享密钥登录门 |

**已知限制**（诚实列一下，不过度承诺）：

- 人设 YAML 里的 `personality`（傲娇度 / 粘人度等数值）目前**尚未接入**任何 Prompt 逻辑，编辑了也不影响猫娘实际表现——这是预留字段，不是能生效的功能。
- 语音识别（FunASR / iFlytek）与语音合成（GPT-SoVITS）依赖外部进程，需要单独启动，不在 `runner.py` / 便携版的编排范围内。
- 便携版不包含 Redis（无官方 Windows 版本），短期对话记忆在服务重启后会清空，长期记忆（Qdrant）不受影响。

---

## 文档索引

| 文档 | 说明 |
| :--- | :--- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 系统总体拓扑、WebGateway 时序图、UML 类图、状态机迁移矩阵、NATS Payload Schema |
| [`docs/API-CONTRACT.md`](docs/API-CONTRACT.md) | 团队多微服务端口隔离规范、HTTP REST 契约、PR 门控检查清单 |
| [`docs/SECURITY.md`](docs/SECURITY.md) | 鉴权环境变量配置与生产环境信任边界清单 |
| [`docs/PORTABLE_PACKAGE.md`](docs/PORTABLE_PACKAGE.md) | 便携版构建与分发指南、覆盖范围与已知限制 |
| [`CHANGELOG.md`](CHANGELOG.md) | 按语义化版本与 Conventional Commits 规范记录的迭代日志 |

---

## 架构图预览

<p align="center">
  <img src="./docs/images/ARCHITECTURE.png" width="49%" alt="System Architecture" />
  <img src="./docs/images/CentralStateMachine.png" width="49%" alt="CentralStateMachine" />
</p>
<p align="center">
  <img src="./docs/images/CognitiveEngine.png" width="49%" alt="CognitiveEngine" />
  <img src="./docs/images/MemoryHub.png" width="49%" alt="MemoryHub" />
</p>

---

## 鸣谢

- **[moeru-ai/airi](https://github.com/moeru-ai/airi)** — 前端数字人框架（stage-web · Live2D · VRM · 通信协议）深度参考来源
- **[gotd/td](https://github.com/gotd/td)** — 高性能 Go Telegram MTProto 客户端
- **[NATS.io](https://nats.io/)** — 超低延迟消息总线，支撑全系统 Pub/Sub 与 JetStream
- **[Qdrant](https://qdrant.tech/)** — 向量数据库，支持语义长期记忆检索
- **[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)** — 本地高质量 TTS 语音合成引擎
- **[Mega Crit Games / Slay the Spire 2](https://www.megacrit.com/)** — 游戏感知 Mod 接入参考
