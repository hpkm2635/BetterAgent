# Project BetterAgent Guide

Concise but detailed reference for contributors and AI agents working across the `BetterAgent` repository. Improve code when you touch it; avoid one-off patterns.

---

## Tech Stack (by surface / subsystem)

- **Go Core (`core/`)**: Go 1.22+, `gotd` (MTProto Telegram client & media streaming), `nhooyr/websocket` / `gorilla/websocket` (WebGateway), `CentralStateMachine` (Per-chat FSM & Deadman Watchdog), `EmotionEngine` (Valence/Arousal/Energy 3D affective engine), `CircadianRhythm` (biological clock decay), `NatsBus` (Go client).
- **Python Services Layer (`services/`)**: Python 3.10+, `asyncio`, `nats-py`.
  - **Cognitive Service (`services/cognitive/`)**: `CognitiveEngine`, `PromptBuilder` (system prompt with mood/circadian injection), `ToolRegistry` (Tools & MCP engine), `BaseLLMProvider` (Gemini, Claude, OpenAI).
  - **Memory Service (`services/memory/`)**: `MemoryHub`, `ShortTermBuffer` (Redis), `VectorMemoryStore` (Qdrant), `UserProfileManager` (user fact personas), `MemoryConsolidator` (Ebbinghaus decay), `TokenBudgetManager` (context trimming).
  - **TTS Service (`services/tts/`)**: TTS audio generation service (GPT-SoVITS / Edge-TTS integration).
- **Frontend (`frontend/`)**: Vue 3 + Vue Router, Vite, TypeScript, Pinia, VueUse, UnoCSS, Vitest, ESLint (pnpm workspace derived from AIRI `stage-web` / Live2D / VRM digital human frontend).
- **Message Infrastructure**: NATS Server (Pub/Sub + JetStream broker).

---

## Structure & Responsibilities

- **Go Core (`core/`)**
  - Entrypoint: `core/cmd/server/main.go`
  - Internal logic: `core/internal/` (`adapters/gotd`, `webgateway`, `antispam`, `humanization`, `csm`, `clock`, `emotion`, `circadian`, `nats`, `schema`).
  - NATS Payload Definitions: `core/internal/schema/payloads.go` (Go structs for NATS EventEnvelope and payload data contracts).
- **Python Services (`services/`)**
  - Runner / Orchestrator: `runner.py` (Launches Python cognitive, memory, and TTS services).
  - Cognitive: `services/cognitive/`
  - Memory: `services/memory/`
  - TTS: `services/tts/`
- **Frontend (`frontend/`)**
  - Web App: `frontend/apps/stage-web/`
  - Shared UI & Engines: `frontend/packages/`, `frontend/engines/`
  - Router / Vite config: `frontend/apps/stage-web/vite.config.ts`
  - Styles & Tokens: `frontend/uno.config.ts`
- **Configuration & Personas (`config/`)**
  - Global config: `config/config.yaml`
  - Persona YAMLs: `config/persona/*.yaml` (e.g. `catgirl.yaml`, `patra.yaml`)
- **Single Source of Truth (`docs/ARCHITECTURE.md`)**
  - Complete SRS and architecture spec including microservice topology, sequence diagrams, state machine transition diagrams, and NATS payload schemas.

---

## Key Path Index & Log Reference

### Key Code Paths
- `docs/ARCHITECTURE.md`: Architecture specification & SRS.
- `core/cmd/server/main.go`: Go Core server entrypoint.
- `core/internal/schema/payloads.go`: Go NATS schemas & event payload contracts.
- `core/internal/csm/`: Per-Chat Central State Machine & Watchdog.
- `core/internal/emotion/`: Affective & physiological computing engine.
- `services/cognitive/`: Python LLM reasoning & prompt building.
- `services/memory/`: Python short-term (Redis) & long-term (Qdrant) memory.
- `services/tts/`: Python TTS voice synthesis service.
- `frontend/apps/stage-web`: Vue 3 Web frontend.
- `config/config.yaml`: Main configuration file.

### Real-Time Log Directory (`logs/`)
Real-time operational logs are aggregated in [`logs/`](file:///d:/projects/BetterAgent/logs). When diagnosing runtime errors or verifying microservice execution, inspect these log files directly:
- `logs/betteragent_core.log`: Go Core general logs.
- `logs/betteragent_core_stderr.log` / `logs/betteragent_core_err.log`: Go Core error logs.
- `logs/cognitive_service.log` / `logs/cognitive_service_stderr.log`: Cognitive Service output & stack traces.
- `logs/memory_service.log` / `logs/memory_service_stderr.log`: Memory Service output & database logs.
- `logs/tts_service.log` / `logs/tts_service_stderr.log`: TTS Service audio generation logs.
- `logs/nats_server.log`: NATS broker logs.
- `logs/fatal.log`: Unhandled system crashes or startup fatal exceptions.

---

## Commands

### Go Core (`core/`)
- **Run Go Core**: `cd core && go run cmd/server/main.go`
- **Typecheck & Tests**: `cd core && go test ./...`
- **Static Vet**: `cd core && go vet ./...`

### Python Services (`services/` & root)
- **Run Python Services**: `python runner.py`
- **Run Tests**: `pytest`

### Frontend (`frontend/`)
- **Dev Server**: `pnpm --filter @proj-airi/stage-web dev`
- **Typecheck**: `pnpm -F @proj-airi/stage-web typecheck`
- **Lint & Fix**: `pnpm lint` / `pnpm lint:fix`

---

## Architecture Compliance & Cross-Language Rules

1. **Single Source of Truth (`docs/ARCHITECTURE.md`)**:
   - Any modifications to NATS subjects (`agent.*`), payload fields, or State Machine transitions (`IDLE`, `THINKING`, `TALKING`, `SLEEPING`, `EXECUTING_ACTION`, `ERROR_RECOVERY`) MUST be reflected in `docs/ARCHITECTURE.md`.
2. **NATS Payload Type Consistency**:
   - All Telegram ID fields (`chat_id`, `user_id`) MUST use `int64` (64-bit integer) across both Go (`core/internal/schema/payloads.go`) and Python services. Never cast them to 32-bit integers or floats.
3. **Per-Chat State Isolation & Watchdog**:
   - State machine changes in Go MUST enforce per-chat concurrency safety (`map[int64]*ChatStateMachine` + `sync.RWMutex`).
   - All long-running reasoning or tool execution states must support the 45-second Deadman Switch Watchdog for automatic recovery.

---

## Development Practices & Code Regulations

### Workarounds
- Every workaround must use this `// NOTICE:` format:
  ```ts
  // NOTICE:
  // Why this workaround is needed.
  // Root cause summary.
  // Source/context (file, issue, URL, or node_modules reference).
  // Removal condition (when it can be safely deleted).
  ```

### Go Coding Regulations
- Explicitly handle all `error` returns; never discard errors with `_`.
- Protect all shared struct state with `sync.RWMutex` or channel messaging.
- Keep Go packages focused; do not split code by execution order alone.

### Python Coding Regulations
- Use explicit Type Hints (`from typing import Optional, List, Dict`).
- Ensure proper cleanup and exception handling in `asyncio` tasks.
- Keep Valibot / Pydantic schemas close to consumers.

### Readability, Naming, and Comments
- Use kebab-case for file names.
- Name functions after domain operations rather than implementation layers.
- Comments should explain information the code cannot express clearly: intent, constraints, ownership, invariants, precedence, lifecycle, ordering, side effects, or non-obvious fallbacks. Do not add comments that merely restate names or visible code.
- Conventional Commits MUST be used for git commit messages (e.g. `feat(core): add emotion circadian decay`, `fix(cognitive): handle tool call timeout`).
