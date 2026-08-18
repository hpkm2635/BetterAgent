# Changelog (变更日志)

All notable changes to the `BetterAgent` project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) & [Conventional Commits](https://www.conventionalcommits.org/).

---

## [Unreleased] - 2026-08-18

### Added & Refactored (Production Debt Remediation — Sprints 1 ~ 7)
- **Memory Service Modernization (Sprint 1 - Storage & Pipelines)**:
  - Upgraded Redis operations to `redis.asyncio` async connection pooling (`short_term_buffer.py`).
  - Implemented `AsyncQdrantClient` vector store in `services/memory/vector_store.py` with multi-provider embedding fallback (OpenAI/Gemini/HashedNgram) and Ebbinghaus decay scoring.
  - Implemented active memory consolidation in `services/memory/consolidator.py` writing LLM-extracted facts to vector store.
  - Added Redis Hash persistence for `user_profile.py` user facts.
- **Context Budget & Fault Tolerance (Sprint 2)**:
  - Added CJK-weighted character token estimation (`estimate_tokens`) in `token_budget.py` to prevent token overflow on Asian languages.
  - Narrowed `SentenceSegmenter` JSON barrier regex to prevent false-positive suppression of valid text containing `{`.
  - Enforced `is_final=True` fallback payloads in `stream_reasoning_loop` exception handlers to unblock Go Core CSM watchdogs.
- **Campus KB Integration (Sprint 3)**:
  - Added concurrent `asyncio.gather` context enrichment for personal RAG, Campus KB (`:8093`), and User Profile in `memory_hub.py`.
  - Added service readiness probes and supervisor launch for `campus_kb_service` in `runner.py`.
- **Self-Memory & Persona Cleanup (Sprint 4)**:
  - Integrated `AgentSelfMemory` tracking in `memory_hub.py` for post-action reflection.
  - Replaced hardcoded persona names with config injection `get_config_val("persona.default_user_name", "主人")`.
- **LLM Provider Interface Normalization (Sprint 5)**:
  - Defined `generate_stream()` abstract contract and `supports_vision()` capability hooks in `BaseLLMProvider`.
  - Refactored `ClaudeProvider` to full `anthropic.AsyncAnthropic` streaming tool-calling engine with API key safety warnings and ID correlation.
  - Removed thread pool blocking `loop.run_in_executor` in `GeminiProvider.generate()` by delegating to `generate_stream()`.
- **MCP Subprocess Lifecycle & Anti-Hang (Sprint 6)**:
  - Added background `presenter_sweep_loop` in `main.py` calling `sweep_idle()` every 60s to prevent orphaned PPT/VSCode child process leaks.
  - Added `asyncio.wait_for` timeout guards to `McpSession.start()` and `McpSession.call_tool()` preventing infinite coroutine stalls.
- **Prompt Optimization & Token Reduction (Sprint 7)**:
  - Added TTL in-memory caching to `PersonaLoader` eliminating per-request YAML file disk I/O.
  - Optimized `PromptBuilder.build_system_prompt()` to strip non-game memory/KB sections during `game_turn` (saving 100-500 tokens/turn).
  - Compacted `agent_self_events` to retain latest 1-2 detailed actions while aggregating historical actions into counter summaries (saving 60-80% tokens).
- **Persona Real-Time Control Panel & Emotion HUD (Sprint A - Frontend & Hot-Reload)**:
  - Added 4-Tab Persona Control Panel UI (`/settings/persona`) for online System Prompt editing, basic identity settings, tsundere/clingy weight compilation, and interactive boundaries.
  - Implemented dual-channel update pipeline: Admin REST API (`:8094`) HTTP PATCH for disk YAML persistence + WebSocket `admin.persona_update` frame to Go Core, publishing NATS `agent.persona.update` for zero-downtime Python `PersonaLoader` in-memory hot reload.
  - Added `EmotionHUDWidget.vue` floating widget displaying live 3D VAD metrics, Affection, Energy, Social Battery, and Jealousy status driven by enriched `AgentEmotionPayload` WebSocket frames.
  - Added prompt compilation header stripping (`stripCompiledHeader`) to guarantee 100% idempotency across repeated save operations.
  - Moved `VisionPrivacyIndicator.vue` to top-left (`left: 16px`) to resolve UI button overlap.
- **API Contract & Team Subservice Boundary (`docs/API-CONTRACT.md`)**:
  - Defined rigid HTTP REST interface specs and port assignments for Campus KB (`:8093`), Admin Panel (`:8094`/`:8095`), and Companion Tool Service (`:8096`).
  - Added automated integration tests (`tests/test_api_contract.py`) for PR merge gating.
- **Slay the Spire 2 Game Perception & Autonomous Gameplay (`services/game_watcher`)**:
  - Implemented `sts2_poller.py` for turn-based state sync and autonomous `game_turn` triggering.
  - Added Go Core HTTP/WS bridge (`core/internal/webgateway/game_state_handler.go`) for real-time game state streaming to `stage-web`.
  - Integrated `Sts2HttpClient` and `sts2_action_tool` with multi-card batch execution optimization in Python Cognitive Service.
  - Added `frontend/apps/stage-web/src/stores/modules/sts2-game-state.ts` Vue Pinia store for live game HUD overlay.

### Changed
- **Cognitive Prompt Protocol & Safety Preamble (`services/cognitive/prompt_builder.py`)**:
  - Hardened system prompt against prompt injection and directory traversal attacks on media parameters.
  - Enhanced chain-of-thought protocol `<thought>` parsing and stage direction filtering in `cognitive_engine.py`.
- **NATS Subject-Graded Action Routing (`shared/subjects.py` & `core/internal/bus/nats_bus.go`)**:
  - Refactored `agent.action.{channel}.{chat_id}` subject routing to prevent cross-channel message leakage between Web and Telegram.

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
