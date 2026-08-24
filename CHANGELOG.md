# Changelog (变更日志)

All notable changes to the `BetterAgent` project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) & [Conventional Commits](https://www.conventionalcommits.org/).

---

## [Unreleased]

### Added
- **iFLYTEK 科大讯飞 STT Provider (`services/stt/`)**：
  - 新增 iFLYTEK WebSocket 流式 STT Provider，支持从浏览器 ScriptProcessorNode 采集 PCM 并通过 WebSocket 推送至后端实时转写。
  - 缺少 iFLYTEK 凭据时自动回退至 FunASR 离线 Provider。
- **MCP Presenter 会话复活 (`services/mcp_ppt/`)**：
  - 优化 presenter_manager 会话重激活逻辑与 Win32 窗口 Docking 机制。

### Fixed
- **Admin 用户/会话列表过滤**：过滤遗留 mock 测试用 `chat_id` 与用户画像，避免测试数据污染 B 端控制台。
- **STT AudioWorklet**：改用 ScriptProcessorNode 规避 iFLYTEK 下 AudioWorklet 采集无声问题；添加 AudioContext 恢复状态日志。
- **Go Core WebGateway**：PCM 边缘平滑处理 + 真正 GPT-SoVITS 分块流式推送 + generation_id 同步修复。

---

## [v1.9.0] - 2026-08-24

针对 `docs/ARCHITECTURE.md` 的一轮完整代码审查发现的严重缺陷、并发缺陷与死代码，分四轮修复：核心链路 Bug 修复、剩余缺陷与死代码清理、STT 中间转写全链路打通、文档同步。

### Added
- **BYOK Provider 配置热更新消费端 (`services/cognitive/`)**：订阅 Admin 后端发布的 `agent.config.reloaded`，调用 `ProviderFactory.invalidate_cache()` + `CognitiveEngine.refresh_default_provider()`，切换 LLM Provider/API Key 无需重启服务。
- **STT 中间转写结果（partial transcript）全链路打通**：Go `nats_bridge.go` 新增订阅 `agent.stt.stream_partial` 并转发为 WS `agent.stt_transcript`（`is_final:false`）；前端 `betteragent-gateway.ts` store 新增 `partialTranscript` 响应式状态（UI 展示留待后续实现）。

### Fixed
- **人设热更新链路打通**：修复 `admin/backend/main.py` 的 `.env` 加载顺序崩溃；`patch_persona` 补上写盘成功后向 NATS 发布 `agent.persona.update`（响应体新增 `hot_reload` 字段）；修复 `services/cognitive/main.py` 缺失的 `PersonaLoader` 导入（此前每次热更新消息都会静默抛 `NameError`）。
- **Barge-in 打断正确性**：去掉 `generation_id == 0` 时跳过过期校验的漏洞；修复 `memory_hub.py` 未转发 `generation_id` 导致被 pydantic 默认值重置的问题；前端追踪逐 chat 的 `generation_id` 并丢弃过期二进制音频帧；`agent.state_change` 的 idle 事件新增 `reason` 字段以区分"正常说完"与"被打断"，避免误伤正常收尾的音频；二进制音频帧改为携带该帧自身的 generation（而非当前 generation）。
- **Companion 推荐接口时区 Bug**：修复 `recommendation.py` 时区朴素时间戳与带时区时间戳相减抛未捕获 `TypeError`（导致 `/api/companion/recommendations` 500）的问题，统一按 `Asia/Shanghai` 解释朴素时间戳。
- **Go 情绪状态数据竞争与跨会话状态错用**：新增 `EmotionalState.GetMoodTag()`/`GetAffectionLevel()` 加锁 getter 替换多处锁外裸读；修复 `game_event_handler.go` 与 `game_turn_handler.go` 两处濒死/胜负事件误用全局兜底情绪状态而非目标会话专属状态的问题。
- **campus_kb 并发 ingest 缺陷**：`KnowledgeStore.ingest()` 新增 `asyncio.Lock`，改为写入后再排除新写入 ID 删除旧内容（write-before-delete），修复并发重复入库、重新入库时短暂出现空窗口、以及批量入库中单个来源清理失败会中止整批处理的问题。
- **跨会话记忆泄漏**：`AgentSelfMemory.self_events` 由进程级扁平列表改为按 `chat_id` 分桶，避免不同会话的自我事件互相串场。
- **admin 后端 `.env` 加载崩溃**、**runner.py 误杀 Docker 托管端口进程**（端口列表移除 Docker 管理的 `10095`/已废弃的 `50000`，加入 companion 服务的 `8096`）。

### Changed
- 清理一批确认无调用点、无架构文档依据的死代码（Go：`CentralStateMachine.GetCurrentState`/`TransitionTo` 旧接口、`SessionManager.BroadcastText`、`MediaManager.CleanOldFiles`、重复的 `SubjectWebUserInterrupt`；Python：`campus_kb/retrieval.py` 的 `rrf_fusion()`、若干未使用的 import），对"没有调用点但看起来是预留接口"的代码保守保留不删。

---

## [v1.8.0] - 2026-08-22

### Added (Supervisor、Admin Panel & Companion 全面集成)
- **Supervisor & Admin Panel Integration (`runner.py`)**:
  - 集成 Admin Backend REST Service (`:8094`) 与 Admin Frontend Vue Service (`:8095`) 到 `runner.py` 进程守护，支持跨平台 CLI 路径探测（`find_cli_cmd` / `shutil.which`）。
  - 新增 `:8094`、`:8095`、`:5173` 端口占用的启动前清理逻辑。
- **Companion Schedule & Memory Integration (`services/companion/`)**:
  - 集成 `ScheduleHUDWidget.vue` 浮动日程小组件与 `Schedule.vue` 操作按钮至 `stage-web`。
  - 新增 `companion_tool.py`，使 Cognitive LLM 可通过 HTTP 调用 `:8096` 管理用户日程。
  - 新增 `schedule-api.ts` 与 `memory-api.ts`，支持前端直接访问记忆与提醒接口。
- **Gotd MTProto 无会话文件优雅处理 (`core/internal/gotd/`)**:
  - 实现 `HasSessionFile()` 检查，`gotd.session.json` 不存在时跳过 MTProto 登录，避免首次启动 panic。
- **API Contract & Team Subservice Boundary (`docs/API-CONTRACT.md`)**:
  - 定义 Campus KB (`:8093`)、Admin Panel (`:8094`/`:8095`)、Companion (`:8096`) 的 HTTP REST 接口规范与端口隔离策略。
  - 新增自动化集成测试 (`tests/test_api_contract.py`) 作为 PR 合并门控。

### Fixed
- **Frontend Build**：升级 `stage-web` 与 Admin Frontend 的 Vite 构建依赖。
- **MobileInteractiveArea.vue / ChatArea.vue**：新增空 `providerId` 防护，避免 WebSocket 桥模式下控制台报错。
- **Admin Backend (`admin/backend/main.py`)**：Qdrant API Key 头部、WebGateway chat_id 命名空间、用户画像 key 命名空间对齐修复；补充模块级 `QDRANT_API_KEY` 变量定义。
- **Memory Service**：强制 Redis RESP2 协议兼容旧版 Windows Redis Server。

---

## [v1.7.0] - 2026-08-20

### Added (Sprint A — 前端人设控制面板 & Emotion HUD)
- **4-Tab 人设控制面板 (`/settings/persona`)**：
  - 支持在线 System Prompt 编辑、基础身份设置、tsundere/clingy 权重编译与交互边界配置。
  - 双渠道更新管道：Admin REST API (`:8094`) HTTP PATCH 持久化 YAML 磁盘 + WebSocket `admin.persona_update` 帧发送至 Go Core，触发 NATS `agent.persona.update` 广播，实现 Python `PersonaLoader` 零停机内存热重载。
  - 新增 `stripCompiledHeader` 函数，保证编译后 Prompt 头部被正确剥离，重复保存时 100% 幂等。
- **EmotionHUDWidget.vue**：
  - 浮动 HUD 组件，实时显示 3D VAD 指标（Valence/Arousal/Dominance）、Affection、Energy、Social Battery 与 Jealousy 状态，由 `AgentEmotionPayload` WebSocket 帧驱动。
- **VisionPrivacyIndicator.vue**：移动至左上角 (`left: 16px`) 解决与其他 UI 按钮重叠问题。

---

## [v1.6.0] - 2026-08-19

### Added (Sprint 7 — Prompt 优化 & Token 裁剪)
- **PersonaLoader TTL 内存缓存**：消除每次请求的 YAML 文件磁盘 I/O。
- **`PromptBuilder.build_system_prompt()` 按场景裁剪**：`game_turn` 模式下跳过非游戏记忆与 KB 章节，节省 100-500 tokens/轮。
- **`agent_self_events` 压缩**：保留最新 1-2 条详细动作，历史动作聚合为计数摘要，节省 60-80% tokens。

---

## [v1.5.0] - 2026-08-18

### Added & Fixed (Sprint 6 — MCP 子进程生命周期防挂起)
- **`presenter_sweep_loop`**：`main.py` 新增后台扫描协程，每 60s 调用 `sweep_idle()` 防止 PPT/VSCode 孤儿子进程泄漏。
- **`asyncio.wait_for` 超时守卫**：为 `McpSession.start()` 与 `McpSession.call_tool()` 添加超时门控，防止协程永久挂起。

---

## [v1.4.0] - 2026-08-18

### Added & Refactored (Sprint 5 — LLM Provider 接口标准化)
- **`BaseLLMProvider` 抽象契约**：定义 `generate_stream()` 抽象方法与 `supports_vision()` 能力 Hook。
- **`ClaudeProvider` 全异步重构**：切换至 `anthropic.AsyncAnthropic` 流式工具调用引擎，添加 API Key 安全警告与 ID 关联校验。
- **`GeminiProvider` 去阻塞化**：移除 `loop.run_in_executor` 线程池阻塞，委托给 `generate_stream()`。

---

## [v1.3.0] - 2026-08-17

### Added (Sprint 4 — 自我记忆 & 人设清理)
- **`AgentSelfMemory`**：在 `memory_hub.py` 中集成 Agent 自我行为反思追踪。
- **Persona 名称去硬编码**：将硬编码人名替换为 `get_config_val("persona.default_user_name", "主人")` 配置注入。

---

## [v1.2.0] - 2026-08-17

### Added (Sprint 3 — Campus KB 集成)
- **并发上下文增强**：在 `memory_hub.py` 中使用 `asyncio.gather` 并发执行个人 RAG、Campus KB (`:8093`) 与 UserProfile 上下文注入。
- **服务就绪探针**：在 `runner.py` 中为 `campus_kb_service` 添加启动探针与 Supervisor 管理。

---

## [v1.1.0] - 2026-08-16

### Added & Refactored (Sprint 1-2 — 记忆现代化 & Token 预算)
- **Redis 异步连接池**：将 `short_term_buffer.py` 升级至 `redis.asyncio` 连接池。
- **AsyncQdrantClient**：在 `services/memory/vector_store.py` 中实现异步向量存储，支持多 Provider 嵌入回退（OpenAI/Gemini/HashedNgram）与 Ebbinghaus 衰减评分。
- **主动记忆归档 (`consolidator.py`)**：实现 LLM 提取事实写入向量存储的主动归档流程。
- **UserProfile Redis Hash 持久化**：在 `user_profile.py` 中实现用户事实画像的 Redis Hash 持久化。
- **CJK 加权 Token 估算 (`token_budget.py`)**：防止亚洲语言文本 Token 溢出。
- **`SentenceSegmenter` JSON 边界正则修复**：收窄正则，防止包含 `{` 的有效文本被误判截断。
- **`stream_reasoning_loop` 异常处理**：强制补发 `is_final=True` 兜底 Payload，解除 Go Core CSM 看门狗因异常阻塞的情形。

---


## [v1.0.0] - 2026-08-15

### Added
- **Go Core Microservice Engine (`core/`)**:
  - `CentralStateMachine`: Per-chat concurrency isolation (`sync.RWMutex`) with 45s Deadman Switch Watchdog timer.
  - `EmotionEngine`: 3D VAD (Valence, Arousal, Dominance) affective model with physical indicators (Energy, SocialBattery, Affection).
  - `CircadianRhythm`: Biological clock decay evaluator driving night/sleep prompt switches.
  - `UrgeEngine`: Boredom and game event energy accumulator triggering proactive speech opportunities (`agent.inbound_message`).
  - `GotdAdapter`: High-concurrency Telegram MTProto client with automatic typing heartbeats and media IO.
  - `WebGateway`: Full-duplex WebSocket gateway (:8080) for web frontends (`stage-web` / Live2D / VRM).
- **Python Services Layer (`services/`)**:
  - `CognitiveService`: Multi-provider LLM engine (Gemini 2.5/3.0, Claude 3.5/3.7, OpenAI) supporting streaming function calling, TTS speech generation, and ImageGen.
  - `MemoryService`: Redis short-term conversation buffer + Qdrant vector memory store + UserProfile fact extractor + Ebbinghaus memory consolidation (`MemoryConsolidator`).
  - `TTSService` & `STTService`: Real-time cancelable streaming voice synthesis (GPT-SoVITS / Edge-TTS) with viseme lip-sync packet generation and FunASR speech-to-text.
- **Frontend Digital Human Application (`frontend/apps/stage-web`)**:
  - Vue 3 + TypeScript + UnoCSS frontend with Live2D/VRM avatar rendering, real-time audio chunk playback, emotion expression binding, and WebSocket status sync.
- **Process Supervisor (`runner.py`)**:
  - Production-grade process orchestrator with win32 Job Object cleanup, circuit breaker restart backoff, and non-blocking VT100 log formatting.
