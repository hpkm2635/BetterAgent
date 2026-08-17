# Changelog (变更日志)

All notable changes to the `BetterAgent` project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) & [Conventional Commits](https://www.conventionalcommits.org/).

---

## [Unreleased] - 2026-08-17

### Added
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
