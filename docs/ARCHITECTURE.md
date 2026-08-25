# BetterAgent (猫娘 Agent) 系统架构与 SRS 规范

本文档为 `BetterAgent` 项目的文本化架构规范与系统需求说明书（SRS）。所有图纸均使用 **Mermaid** 格式描述，可以直接进行 Git 版本控制与 AI Agent 语义解析，防止设计资产“文档腐化”。

> 各组件间的鉴权机制、信任边界与部署前检查清单见配套文档 [SECURITY.md](./SECURITY.md)。图中标注的 NATS / WebGateway 等通道均已要求鉴权，细节以该文档为准。

---

## 1. 系统总体拓扑架构 (System Topology & Microservices)

针对生产环境的高并发网络 IO 与复杂 LLM 推理需求，系统采用 **Go Core (高性能控制核) + NATS (中枢消息总线) + Python Services (认知与记忆微服务) + Team Microservices (团队隔离外包/拓展微服务)** 的异构架构。

```mermaid
graph TD
    subgraph External_Adapters["External IO Networks / Clients / Games"]
        TG["Telegram Cloud API"]
        WebClient["Browser Client (stage-web / Live2D / VRM / 端口 5173)"]
        STS2Game["Slay the Spire 2 Game (C# Mod)"]
    end

    subgraph Go_Core["Go Core (betteragent-core)"]
        GotdAdapter["GotdAdapter (MTProto IO / typing心跳 / 媒体收发)"]
        WebGateway["WebGateway (WebSocket 网关 / 全双工事件桥接 / 端口 8080)"]
        AntiSpam["AntiSpamGuard (令牌桶全局/Peer限流)"]
        HumanEngine["HumanizationEngine (拟人打字延迟模拟)"]
        CSM["CentralStateMachine (中央状态机: IDLE/THINKING/SLEEPING...)"]
        ClockEngine["ClockEngine (TICK 定时心跳)"]
        EmotionEngine["EmotionEngine (VAD 3D情绪模型 & 生理指标)"]
        CircadianEvaluator["CircadianRhythm (昼夜生物钟评估器)"]
        UrgeEngine["UrgeEngine (欲望/枯燥度累加器 & 主动开口决策)"]
        GameEventIngest["GameEventHandler / GameStateHandler (HTTP:8090 / WS)"]
        GoNatsBus["NatsBus Client (Go)"]

        GotdAdapter --> AntiSpam
        GotdAdapter --> HumanEngine
        ClockEngine --> CircadianEvaluator
        CircadianEvaluator --> EmotionEngine
        EmotionEngine --> UrgeEngine
        UrgeEngine --> CSM
        GameEventIngest --> UrgeEngine
        GameEventIngest --> GoNatsBus
        EmotionEngine --> CSM
        CSM --> GoNatsBus
        GotdAdapter --> GoNatsBus
        WebGateway --> GoNatsBus
    end

    subgraph Message_Broker["Message Infrastructure"]
        NATS["NATS Server (Pub/Sub + JetStream / 端口 4222)"]
    end

    subgraph Core_Python_Services["Core Python Services Layer"]
        PyNatsBus["NatsBus Client (Python)"]
        
        subgraph Cognitive_Service["Cognitive Service (端口 8091 TTS / 8092 STT)"]
            CognitiveEngine["CognitiveEngine (推理逻辑)"]
            PromptBuilder["PromptBuilder (System Prompt组装)"]
            ToolRegistry["ToolRegistry (Tools / MCP / RAG / Game Action)"]
            LLMProvider["BaseLLMProvider (Gemini / Claude / OpenAI)"]

            CognitiveEngine --> PromptBuilder
            CognitiveEngine --> ToolRegistry
            CognitiveEngine --> LLMProvider
        end

        subgraph Memory_Service["Memory Service"]
            MemoryHub["MemoryHub (记忆编排)"]
            ShortTermBuffer["ShortTermBuffer (Redis短时缓存)"]
            VectorStore["VectorMemoryStore (Qdrant向量检索)"]
            UserProfile["UserProfileManager (用户事实画像)"]
            Consolidator["MemoryConsolidator (艾宾浩斯遗忘/记忆归档)"]
            TokenBudget["TokenBudgetManager (上下文 Token 剪裁)"]

            MemoryHub --> ShortTermBuffer
            MemoryHub --> VectorStore
            MemoryHub --> UserProfile
            MemoryHub --> Consolidator
            MemoryHub --> TokenBudget
        end

        subgraph Game_Watcher_Service["Game Watcher Service"]
            STS2Poller["STS2 Poller (轮询 C# Mod 状态并触发 Game Turn)"]
        end

        PyNatsBus --> CognitiveEngine
        PyNatsBus --> MemoryHub
        STS2Poller --> GoNatsBus
    end

    subgraph Team_Microservices["Team Isolated Microservices (API Contract Managed)"]
        CampusKB["Campus KB RAG Service (冯文哲 / HTTP:8093)"]
        AdminPanel["Admin Panel Service (谢自立 / REST:8094 & Web:8095)"]
        CompanionService["Companion Tool Service (张劭哲 / HTTP:8096 & SQLite)"]
    end

    TG <--> GotdAdapter
    WebClient <-->|"WebSocket (全双工)"| WebGateway
    STS2Game <-->|"HTTP:8090 / C# Mod"| GameEventIngest
    STS2Game <-->|"HTTP / Game Tool Calls"| ToolRegistry
    
    GoNatsBus <--> NATS
    PyNatsBus <--> NATS

    ToolRegistry --"HTTP POST /api/kb/search"--> CampusKB
    AdminPanel --"HTTP Proxy /api/kb/*"--> CampusKB
    AdminPanel --"PATCH Persona YAML"--> Core_Python_Services
    CompanionService --"POST Stat & Reminder Trigger"--> Go_Core
```

---

## 2. 消息处理与推理时序 (Sequence Diagrams)

### 2.1 Web 前端全双工数字人流式交互时序 (WebGateway & Digital Human Audio/Viseme, 核心主路线)

网页前端（`stage-web`）通过 WebSocket (`:8080`) 连接 `WebGateway`，实现**音视频切片流、Live2D Viseme 口型同步、流式状态机与双工多模态交互**：

```mermaid
sequenceDiagram
    autonumber
    actor WebUser as Web Browser User (stage-web)
    participant GW as WebGateway (Go WebSocket 网关 :8080)
    participant NATS as NATS Message Bus
    participant CSM as CentralStateMachine (Go)
    participant Memory as MemoryHub (Python)
    participant Cog as CognitiveEngine (Python)
    participant TTS as TTS Service (Python :8091)

    WebUser->>GW: WebSocket 文本消息 / VAD 语音切片
    Note over GW: idspace 分配/映射 Web session_id ➔ int64 内部 ID
    GW->>NATS: Publish "agent.inbound_message" (source_channel="web", generation_id=N)
    
    NATS-->>CSM: Notify "agent.inbound_message"
    CSM->>CSM: 状态切换 TransitionTo(THINKING)
    CSM->>NATS: Publish "agent.enrich_context_req" (generation_id=N)
    
    NATS-->>Memory: Notify "agent.enrich_context_req"
    Memory->>NATS: Publish "agent.reasoning_request" (generation_id=N)

    NATS-->>Cog: Notify "agent.reasoning_request"
    Cog->>Cog: 流式生成 Text Delta
    Cog->>NATS: Publish "agent.action.web.{chat_id}" (ActionDecisionPayload, generation_id=N)

    NATS-->>TTS: Notify "agent.action.web.*"
    TTS->>TTS: 实时合成 PCM 音频切片 + 生成 Viseme 口型帧
    TTS->>NATS: Publish "agent.audio.chunk" (内嵌 visemes 字段, generation_id=N)

    NATS-->>GW: Notify "agent.audio.chunk" & "agent.action.web.*"
    Note over GW: 校验 generation_id == 当前最新，丢弃在途过期旧帧
    GW-->>WebUser: WebSocket 双工下发 { audio_base64, visemes, text_delta, emotion_tag }
    
    Note over WebUser: 浏览器底层 AudioContext 播放音频 + Live2D 唇形模型实时渲染

    TTS->>NATS: Publish "agent.tts.stream_end"
    GW->>NATS: Publish "agent.action_completed"
    NATS-->>CSM: Notify "agent.action_completed" (状态恢复 IDLE)
```

### 2.2 Barge-in 用户打断撤销时序 (Realtime Stream Cancel & Generation Increment)

当数字人正在说话/播放音频时，用户说话或在界面点击“打断”，系统毫秒级撤销在途推理，并**递增 generation_id 清空过期队列**：

```mermaid
sequenceDiagram
    autonumber
    actor WebUser as Web Browser User
    participant GW as WebGateway (Go)
    participant NATS as NATS Message Bus
    participant CSM as CentralStateMachine (Go)
    participant TTS as TTS Service (Python)

    WebUser->>GW: VAD 检测用户开口 / 点击打断 (Barge-in)
    GW->>CSM: IncrementGeneration() ➔ generation_id = N+1
    GW->>NATS: Publish "agent.user.interrupt" & "agent.stream.cancel_req" (generation_id=N+1)
    
    NATS-->>CSM: Notify "agent.user.interrupt"
    CSM->>CSM: 立即状态切换 TransitionTo(CANCELLING)
    
    NATS-->>TTS: Notify "agent.stream.cancel_req"
    TTS->>TTS: 终止当前在途音频合成线程 / 清空 Chunk 队列
    TTS->>NATS: Publish "agent.stream.cancel_ack"

    GW-->>WebUser: WebSocket 发送 { type: "agent.state_change", payload: { state: "idle", reason: "stream_cancelled" } }
    Note over WebUser: 前端依据 reason="stream_cancelled" 立刻停止 AudioContext 播放（reason 字段用于区分"被打断"与"正常说完"两种 idle，避免误伤正常收尾的音频）

    CSM->>CSM: 状态清空并重新 TransitionTo(THINKING) (带着最新打断文本重新推理)
```

### 2.3 Telegram 消息处理时序 (Gotd Adapter, 二级异步扩展通道)

用户在 Telegram 发送消息后，Go Core、NATS、Memory Service 与 Cognitive Service 之间的完整 Pub/Sub 交互时序：

```mermaid
sequenceDiagram
    autonumber
    actor User as Telegram User
    participant Adapter as GotdAdapter (Go)
    participant NATS as NATS Message Bus
    participant CSM as CentralStateMachine (Go)
    participant Memory as MemoryHub (Python)
    participant Cog as CognitiveEngine (Python)

    User->>Adapter: 发送消息 / 媒体文件
    Note over Adapter: AntiSpamGuard 校验通过
    Adapter->>Adapter: 开启 Typing 心跳后台协程
    Adapter->>NATS: Publish "agent.inbound_message" (source_channel="telegram")
    
    NATS-->>CSM: Notify "agent.inbound_message"
    CSM->>CSM: 状态切换 TransitionTo(THINKING)
    CSM->>NATS: Publish "agent.enrich_context_req"
    
    NATS-->>Memory: Notify "agent.enrich_context_req"
    Memory->>NATS: Publish "agent.reasoning_request"

    NATS-->>Cog: Notify "agent.reasoning_request"
    Cog->>Cog: PromptBuilder 组装带情绪与生理状态的 Prompt
    Cog->>NATS: Publish "agent.action.telegram.{chat_id}" (ActionDecisionPayload)
    
    NATS-->>Adapter: Notify "agent.action.telegram.*" (Adapter 专属通道)
    Adapter->>Adapter: 停止 Typing 心跳协程
    Adapter->>Adapter: HumanizationEngine 模拟拟打字延迟 (基础值按字数计算, 钳制在 0.5s~8.0s, 媒体附加 +1.5s/+2.0s 后再叠加 ±15% 抖动, 实际观测上限可超过 8.0s)
    Adapter->>User: 发送回复文本 / 音频 / 图片
    Adapter->>NATS: Publish "agent.action_completed" (ActionCompletedPayload)

    NATS-->>Memory: Notify "agent.action_completed" (追加短时对话历史)
    NATS-->>CSM: Notify "agent.action_completed" (状态切换 TransitionTo IDLE)
```

---

## 3. 多维会话隔离与代际控制状态机 (Multi-Session Generation State Machine & Watchdog)

系统由 Go Core 实现的 `CentralStateMachine` 掌控，通过 `map[int64]*ChatStateMachine` + `sync.RWMutex` 实现多维度会话隔离与状态生命周期管理，并配备 **Generation ID (代际防死锁与碰撞)** 与 **Deadman Switch Watchdog (超时自愈死人开关)**：

### 3.1 会话隔离与代际控制 (Idspace & Generation Guard)
1. **多渠道 ID 空间 (`idspace`)**：Telegram `chat_id` 与 Web 网关 WebSocket `session_id` 均统一映射为 int64 会话空间，避免 Channel 间会话混淆。
2. **原子代际计数器 (`generation_id`)**：每一轮新消息或 Barge-in 打断触发时，`generation_id` 原性自增。网络网关与消费端校验帧的 `generation_id`，自动滤除在途网络延迟导致的旧代际帧碰撞。
3. **2小时 TTL 自动回收 (`ChatStateInactivityTTL`)**：`IDLE` 状态空闲会话 2 小时后自动从内存中 Evict 释放，防止临时 WebSession 造成内存泄漏。

### 3.2 状态机迁移规范 (State Transition Matrix)

```mermaid
stateDiagram-v2
    [*] --> IDLE : system_started

    IDLE --> LISTENING : VAD_SPEECH_START (麦克风听觉激活)
    LISTENING --> STREAMING_STT : STT_CHUNK_START (开始实时语音转写)
    STREAMING_STT --> THINKING : STT_FINAL (转写完成)
    STREAMING_STT --> CANCELLING : USER_INTERRUPT (打断语音转写)

    IDLE --> THINKING : INBOUND_MESSAGE / TICK [is_proactive_opportunity == True]
    THINKING --> STREAMING_TTS : TTS_STREAM_START (流式音视频开始)
    THINKING --> TALKING : TTS_SINGLE_PLAY
    THINKING --> EXECUTING_ACTION : REASONING_COMPLETED [has_action == True]
    THINKING --> IDLE : REASONING_COMPLETED [action == DO_NOTHING]
    THINKING --> ERROR_RECOVERY : LLM_API_ERROR 或 DEADMAN_TIMEOUT [>45s]

    TALKING --> STREAMING_TTS : TTS_CHUNK_FRAME
    STREAMING_TTS --> IDLE : TTS_STREAM_END (流式播放完毕)
    STREAMING_TTS --> CANCELLING : USER_INTERRUPT (Barge-in 打断撤销)

    TALKING --> IDLE : TTS_STREAM_END (说话播放完毕)
    TALKING --> INTERRUPTED : USER_INTERRUPT (Barge-in 用户打断)
    TALKING --> EXECUTING_ACTION : TOOL_EXECUTION

    INTERRUPTED --> CANCELLING : CANCEL_IN_FLIGHT_REQUESTS (generation_id++)
    CANCELLING --> THINKING : RE_REASONING (带着最新打断输入重新思考)
    CANCELLING --> IDLE : PURGE_RESET

    EXECUTING_ACTION --> IDLE : ACTION_COMPLETED
    EXECUTING_ACTION --> ERROR_RECOVERY : TELEGRAM_API_ERROR 或 DEADMAN_TIMEOUT [>45s]

    IDLE --> SLEEPING : TICK [is_sleep_hours == True] 或 GOODNIGHT_EVENT
    SLEEPING --> THINKING : INBOUND_MESSAGE [加载犯困/梦话 Prompt]
    SLEEPING --> IDLE : TICK [is_wake_time == True]

    IDLE --> MOODY_REST : EMOTION_EVENT [mood < MOODY_THRESHOLD]
    MOODY_REST --> IDLE : INBOUND_MESSAGE [is_apology_or_gift == True] 或 TICK [cooldown_timer_expired]

    ERROR_RECOVERY --> IDLE : RECOVERY_SUCCESS / WATCHDOG_RESET
    ERROR_RECOVERY --> [*] : FATAL_ERROR / SYSTEM_SHUTDOWN
```

---

## 4. NATS 总线与 Payload 强类型 Schema (Payload Schemas)

数据契约通过 NATS EventEnvelope 进行跨语言传输。Go 侧将 Telegram ID 严格限制为 `int64`。

### 4.1 NATS 点号分级规范 (Subject Hierarchy)

| 领域分类 | NATS Subject | 说明 |
| :--- | :--- | :--- |
| **基础中枢** | `agent.tick` / `agent.inbound_message` | 系统心跳 / 上行消息（`agent.error` 全仓库零引用，未实现，勿依赖此主题） |
| **记忆与推理** | `agent.enrich_context_req` / `agent.reasoning_request` | 记忆检索请求 / LLM 推理触发 |
| **决策与执行** | `agent.action.{channel}.{chat_id}` / `agent.action_completed` | 行动决策下发（按渠道分级路由，如 `agent.action.web.1001` / `agent.action.telegram.56789`，各渠道适配器只订阅自己的通配符，NATS 在总线层面完成路由，不再靠 payload 里的 `source_channel` 字段各自过滤）/ 行动完成确认（不分渠道，供 Memory 服务统一记录对话历史） |
| **VAD 听觉** | `agent.speech.start` / `agent.speech.end` | 麦克风开始/结束说话 |
| **打断撤销** | `agent.user.interrupt` / `agent.stream.cancel_req` | 用户打断 (Barge-in) 撤销 |
| **数字人流** | `agent.audio.chunk` | TTS 音频切片，`StreamAudioChunkPayload` 内嵌 `visemes` 字段（口型时间轴数据随音频一起下发；`agent.tts.stream_chunk` / `agent.viseme.data` 两个独立主题代码里从未存在，勿依赖） |
| **流式控制** | `agent.stt.stream_chunk` / `agent.stt.stream_final` / `agent.stt.stream_partial` / `agent.tts.stream_end` | STT 转写流（含边说边出的中间结果，Go 订阅后转发为 WS `agent.stt_transcript` `is_final:false`） / STT 最终转写 / TTS 结束 |
| **流式状态** | `agent.stream.cancel_ack` | 流式撤销确认（浏览器端的状态广播走 WS 消息类型 `agent.state_change`，不经过 `agent.stream.state_change` 这个 NATS 主题——该主题代码里从未发布/订阅过，勿依赖） |
| **视觉与感知** | `agent.vision.frame` / `agent.emotion.delta` | 画面快照 / 动态情绪增量回传 (Cognitive -> Go)（`agent.emotion.update` 目前只有 Go 端订阅、从未有发布方，尚未接通，勿依赖） |
| **人设与配置** | `agent.persona.update` | 人设热更新广播（YAML 磁盘同步 + PersonaLoader 内存缓存失效） |
| **游戏感知** | `agent.game_event` | 外部游戏事件广播（稀有圣物、濒死、胜负结算等） |

### 4.2 系统核心总线与适配器 UML 类图 (Core NATS Bus & Multi-Channel Adapters Class Diagram)

架构全量基于 **NATS 消息总线 (`NatsBus`)** 进行 Go Core 与 Python 异构微服务间解耦，由 **Go Core** 统一掌控 WebSocket 全双工网关 (`WebGateway`)、Telegram Cloud API 适配器 (`GotdAdapter`)、中央状态机 (`CentralStateMachine`) 与多维度会话隔离 (`idspace.WebNamespaceOffset`)：

```mermaid
classDiagram
    class NatsBus_Go {
        <<Go Core NATS Client>>
        -nc *nats.Conn
        -logger *zap.Logger
        +Publish(subject, payload) error
        +Subscribe(subject, handler) *nats.Subscription
        +Request(subject, payload, timeout) (*EventEnvelope, error)
        +Close()
    }

    class NatsClient_Py {
        <<Python Service NATS Client>>
        -nc NATS
        -js JetStreamContext
        +connect(servers)
        +publish(subject, payload_dict)
        +subscribe(subject, cb_coroutine)
        +request(subject, payload_dict, timeout)
    }

    class WebGatewayServer {
        <<Go Core WebSocket 网关 :8080>>
        -addr string
        -token string
        -sessions *SessionManager
        -bridge *NatsBridge
        -urgeEngine *UrgeEngine
        +Start(ctx) error
        +HandleWebSocket(w, r)
    }

    class SessionManager {
        <<Go Session Registry>>
        -mu sync.RWMutex
        -sessions map[int64]*WebSession
        -tokenIndex map[string]int64
        +GetOrCreateSession(wsToken) (int64, *WebSession)
        +GetSession(chat_id) (*WebSession, bool)
        +RemoveSession(chat_id)
    }

    class NatsBridge {
        <<Go WS <-> NATS Bridge>>
        -bus *bus.NatsBus
        -csm *engine.CentralStateMachine
        -sessions *SessionManager
        +Start() error
        +HandleInboundWSText(session_id, text, generation_id)
        +HandleVADSpeechStart(session_id)
        +HandleVADSpeechEnd(session_id)
        +HandleBargeInInterrupt(session_id)
        +ForwardAudioChunkToWS(chat_id, audio_base64, visemes)
        +ForwardSTTTranscriptToWS(chat_id, text, is_final)
    }

    class GotdAdapter {
        <<Go Telegram Cloud API 适配器>>
        -client *telegram.Client
        -bus *bus.NatsBus
        -antiSpam *AntiSpamGuard
        -humanization *HumanizationEngine
        +Start(ctx) error
        +HandleTelegramUpdate(ctx, u)
        +SendTypingHeartbeat(chat_id)
        +HandleActionDecision(payload)
    }

    class CentralStateMachine {
        <<Go Central State Guard>>
        -mu sync.RWMutex
        -chatMachines map[int64]*ChatStateMachine
        -watchdogTimeout time.Duration
        +GetOrCreateMachine(chat_id) *ChatStateMachine
        +TransitionTo(chat_id, newState, reason) error
        +IncrementGeneration(chat_id) uint64
        +ValidateGeneration(chat_id, gen_id) bool
    }

    NatsBridge --> SessionManager : 查表分配 100 亿+ offset 隔离 ID
    NatsBridge --> NatsBus_Go : Publish inbound/interrupt & Subscribe audio/stt/action
    WebGatewayServer "1" *-- "1" SessionManager : 管理全双工 Session 声明周期
    WebGatewayServer "1" *-- "1" NatsBridge : 全双工 WS 事件双向桥接
    GotdAdapter --> NatsBus_Go : Publish inbound & Subscribe agent.action.telegram.*
    CentralStateMachine --> NatsBus_Go : 监听状态变更 & 广播 state_change
    NatsBus_Go <..> NatsClient_Py : 跨语言 NATS Subject 事件交互
```

### 4.3 Payload 数据结构类图 (Payload Schemas)

```mermaid
classDiagram
    class EventEnvelope {
        +String id
        +String subject
        +float timestamp
        +String source
        +Object payload
    }

    class BasePayload {
        +String event_id
        +float timestamp
        +String source_component
    }

    class InboundMessagePayload {
        +int64 chat_id
        +int64 user_id
        +int generation_id
        +int message_id
        +String source_channel
        +String raw_text
        +String file_path
        +String media_type
        +String voice_transcript
    }

    class EnrichContextReqPayload {
        +int64 chat_id
        +int64 user_id
        +int generation_id
        +InboundMessagePayload inbound_message
        +String current_state
        +String trigger_type
        +String emotion_description
        +String source_channel
    }

    class ReasoningRequestPayload {
        +int64 chat_id
        +int64 user_id
        +int generation_id
        +List~Dict~ short_term_history
        +Dict user_profile
        +List~String~ rag_facts
        +String current_emotion
        +String personality_description
        +String circadian_description
        +float mood_score
        +InboundMessagePayload inbound_message
    }

    class EmotionDeltaPayload {
        +int64 chat_id
        +float delta_valence
        +float delta_arousal
        +float delta_affection
        +bool is_jealous
    }

    class StreamChunkPayload {
        +int64 chat_id
        +int generation_id
        +int chunk_index
        +bool is_final
        +String source_channel
        +String text_delta
        +String audio_base64
        +List~Dict~ visemes
        +bool is_sentence_start
    }

    class STTTranscriptPayload {
        +int64 chat_id
        +int generation_id
        +String text
        +String source_channel
    }

    class StreamCancelPayload {
        +int64 chat_id
        +int generation_id
        +String reason
        +String source_channel
    }

    class ActionDecisionPayload {
        +int64 chat_id
        +int generation_id
        +String source_channel
        +String action_type
        +String text_content
        +float typing_delay
        +String voice_path
        +String photo_path
        +bool is_final
    }

    class ActionCompletedPayload {
        +int64 chat_id
        +int sent_message_id
        +ActionDecisionPayload action_decision
        +String status
        +float sent_time
    }

    class GameEventPayload {
        +String game
        +String event_type
        +float weight
        +String detail
        +Dict metadata
    }

    class TickPayload {
        +String iso_time
        +String time_of_day
        +float idle_duration_seconds
        +bool is_sleep_hours
        +int tick_counter
    }

    EventEnvelope --> BasePayload
    BasePayload <|-- InboundMessagePayload
    BasePayload <|-- EnrichContextReqPayload
    BasePayload <|-- ReasoningRequestPayload
    BasePayload <|-- StreamChunkPayload
    BasePayload <|-- STTTranscriptPayload
    BasePayload <|-- StreamCancelPayload
    BasePayload <|-- ActionDecisionPayload
    BasePayload <|-- ActionCompletedPayload
    BasePayload <|-- GameEventPayload
    BasePayload <|-- TickPayload
```

### 4.4 记忆子系统 UML 类图 (Memory Subsystem Class Diagram)

Python `services/memory` 服务负责双层记忆编排、Redis 短时对话缓冲、Qdrant 长期向量衰减检索与用户事实画像提炼：

```mermaid
classDiagram
    class MemoryHub {
        -short_term_buffer: ShortTermMemoryBuffer
        -vector_store: VectorMemoryStore
        -profile_mgr: UserProfileManager
        -consolidator: MemoryConsolidator
        -token_budget: TokenBudgetManager
        -self_memory: AgentSelfMemory
        +async handle_inbound_message(payload: InboundMessagePayload)
        +async handle_action_completed(payload: ActionCompletedPayload)
        +async consolidate_user_memory(user_id: int) Dict
    }

    class ShortTermMemoryBuffer {
        -redis_client: Optional~Redis~
        -buffers: Dict~int, List~Dict~~
        +max_capacity: int = 20
        +async add_message(user_id: int, role: str, content: str, metadata: Dict)
        +async get_recent_messages(user_id: int, limit: int = 20) List~Dict~
        +async get_unconsolidated_messages(user_id: int) List~Dict~
        +async mark_consolidated(user_id: int, count: int)
        +async clear_buffer(user_id: int)
    }

    class VectorMemoryStore {
        -vector_db_client: QdrantClient
        -embedding_provider: BaseEmbeddingProvider
        +collection_name: str = "catgirl_memories"
        +async search_relevant_memories(user_id: int, query_text: str, top_k: int = 5, score_threshold: float = 0.7, decay_lambda: float = 0.01) List~str~
        +async add_memory_segment(user_id: int, text: str, metadata: Dict) str
        +async delete_memory(memory_id: str) bool
    }

    class UserProfileManager {
        -db_session: DatabaseSession
        -profile_cache: Dict~int, Dict~
        +get_profile(user_id: int) Dict
        +update_fact(user_id: int, key: str, value: Any)
        +get_formatted_profile_prompt(user_id: int) str
        +invalidate_fact(key: str)
    }

    class MemoryConsolidator {
        -llm_provider: BaseLLMProvider
        -consolidation_threshold: int = 15
        -user_locks: Dict~int, asyncio.Lock~
        +importance_threshold: float
        +async consolidate(user_id: int, messages: List, vector_store: VectorMemoryStore) Dict
        +async evaluate_importance_and_extract(messages: List) Dict
    }

    class TokenBudgetManager {
        +fit_into_budget(system_prompt, history, profile, rag_facts, max_budget=4000)
    }

    class AgentSelfMemory {
        -self_events: List~Dict~
        +add_self_event(event_description, emotion_tag)
        +get_recent_self_events(limit=3) List~Dict~
    }

    MemoryHub "1" *-- "1" ShortTermMemoryBuffer : 短时对话缓冲 (Redis)
    MemoryHub "1" *-- "1" VectorMemoryStore : 向量检索 (Qdrant + 艾宾浩斯衰减)
    MemoryHub "1" *-- "1" UserProfileManager : 用户画像与事实注入
    MemoryHub "1" *-- "1" MemoryConsolidator : 记忆归档与摘要提炼
    MemoryHub "1" *-- "1" TokenBudgetManager : 上下文 Token 剪裁
    MemoryHub "1" *-- "1" AgentSelfMemory : 猫娘自我事件记录
    MemoryConsolidator ..> VectorMemoryStore : 写入长期向量记忆
    MemoryConsolidator ..> ShortTermMemoryBuffer : 标记已归档消息
```

### 4.5 认知与推理子系统 UML 类图 (Cognitive Subsystem Class Diagram)

Python `services/cognitive` 服务负责提示词动态编译、多 Provider 抽象（Gemini / Claude / OpenAI / DeepSeek）、Tool & MCP 调度与情感反馈闭环：

```mermaid
classDiagram
    class CognitiveEngine {
        -providers: Dict~str, BaseLLMProvider~
        -prompt_builder: PromptBuilder
        -tool_registry: ToolRegistry
        -presenter_mgr: PresenterSessionManager
        +async handle_reasoning_request(payload: ReasoningRequestPayload)
        +async execute_reasoning_loop(messages: List~Dict~, tools_schema: List~Dict~) List~ActionDecisionPayload~
        +parse_thought_and_clean_text(raw_text: str) Tuple~str, str~
        +parse_emotion_delta_from_text(text: str) Tuple~str, Dict~
    }

    class PromptBuilder {
        -base_system_template: str
        +build_system_prompt(payload: ReasoningRequestPayload) str
        +build_messages(payload: ReasoningRequestPayload) List~Dict~
    }

    class ProviderFactory {
        +get_provider(provider_type: str) BaseLLMProvider
        +invalidate_cache()
    }

    class BaseLLMProvider {
        <<Abstract>>
        #api_key: str
        #model_name: str
        +async generate(messages: List~Dict~, tools_schema: List~Dict~ = None) LLMResponsePayload*
    }

    class GeminiProvider {
        -sdk: GoogleGenAI
    }
    class ClaudeProvider {
        -sdk: AnthropicSDK
    }
    class OpenAIProvider {
        -sdk: OpenAISDK
    }
    class DeepSeekProvider {
        -sdk: OpenAISDK
    }

    class ToolRegistry {
        -tools: Map~str, BaseTool~
        +register_tool(tool: BaseTool)
        +get_tool(name: str) BaseTool
        +get_all_schemas() List~Dict~
    }

    class BaseTool {
        <<Abstract>>
        +name: str
        +description: str
        +parameters_schema: Dict
        +async execute(**kwargs: Dict)*
    }

    class TelegramActionTool {
        +async execute(action_type, sticker_id, emoji) Dict
    }
    class TTSTool {
        +async execute(text, emotion) Dict
    }
    class ImageGenTool {
        +async execute(prompt, style) Dict
    }
    class CampusKBTool {
        +async execute(query) Dict
    }
    class CompanionTool {
        +async execute(action, schedule_id) Dict
    }
    class SlayTheSpire2Tool {
        +async execute(command, target) Dict
    }
    class PresenterSessionManager {
        -sessions: Dict~str, PresenterSession~
        +handle_mcp_call(tool_name, arguments)
    }

    CognitiveEngine "1" *-- "1" PromptBuilder : 组装多模态提示词
    CognitiveEngine "1" *-- "1" ToolRegistry : 管理与调度工具
    CognitiveEngine --> ProviderFactory : 获取 LLM 实例
    ProviderFactory ..> BaseLLMProvider : 工厂创建
    BaseLLMProvider <|-- GeminiProvider
    BaseLLMProvider <|-- ClaudeProvider
    BaseLLMProvider <|-- OpenAIProvider
    BaseLLMProvider <|-- DeepSeekProvider
    ToolRegistry "1" *-- "many" BaseTool : 依赖反转注册
    BaseTool <|-- TelegramActionTool
    BaseTool <|-- TTSTool
    BaseTool <|-- ImageGenTool
    BaseTool <|-- CampusKBTool
    BaseTool <|-- CompanionTool
    BaseTool <|-- SlayTheSpire2Tool
    CognitiveEngine "1" *-- "1" PresenterSessionManager : MCP 协议桥接
```

---

## 5. 猫娘“情感与生理”机制 (Affective Computing Engine)

驻留在 Go Core 内部的多 ChatID 隔离心理与生理计算模型，支持多路并发快照隔离与原子落盘自愈机制：

```mermaid
classDiagram
    class EmotionalStateStore {
        -RWMutex mu
        -Map~int64, EmotionalState~ states
        -Map~int64, Time~ lastAccessed
        +GetOrCreate(chat_id) EmotionalState
        +Snapshot() Map~int64, EmotionalState~
        +SaveToFileAtomic(filePath) error
        +LoadFromFileWithRecovery(filePath) error
        +PruneInactive(ttl) int
    }

    class EmotionalState {
        -RWMutex mu
        +float Valence
        +float Arousal
        +float Dominance
        +float Energy
        +float Satiety
        +float SocialBattery
        +float AffectionLevel
        +MoodEnum CurrentMoodTag
        +float JealousyLevel
        +float BaselineValence
        +Time LastUpdated
        +ApplySentimentDelta(delta_v, delta_a, delta_aff)
        +ApplyTimeDecay(elapsed_seconds)
        +ApplySatietyDelta(delta_satiety)
        +SetJealousy(level)
        +IsJealous() bool
        +ToPromptDescription() String
    }

    class EmotionDeltaHandler {
        -NatsBus bus
        -EmotionalStateStore emotionStore
        +Start() error
        +HandleEmotionDelta(payload)
    }

    class PersonalityProfile {
        +float TsundereLevel
        +float Clinginess
        +float JealousyThreshold
        +float CatNature
        +float Neuroticism
        +float Extraversion
        +NewPersonalityFromConfig(cfg)
        +ShouldTriggerJealousy(entities) bool
        +ToPromptDescription() String
    }

    class CircadianRhythmEvaluator {
        +int DayStartHour
        +int NightStartHour
        +EvaluateTimeDecay(elapsed_seconds)
        +GetCircadianFactor(current_hour) float
        +ToPromptDescription() String
    }

    class UrgeEngine {
        -Mutex mu
        +float Value
        +float GameEventEnergy
        +Time CooldownUntil
        +Time DeadZoneUntil
        +RecordGameEvent(weight, reason)
        +OnTurnCompleted()
        +EvaluateTick(now, elapsed, emoState, personality, isSleepHours, targetState, unreadPressure) (bool, string)
    }

    EmotionalStateStore "1" *-- "many" EmotionalState : 管理与持久化
    EmotionDeltaHandler --> EmotionalStateStore : Clamp 钳位更新 (dValence ∈ [-0.3, +0.3], dAffection ∈ [-2.0, +2.0])
    CircadianRhythmEvaluator --> EmotionalState : 驱动昼夜衰减
    PersonalityProfile --> EmotionalState : 影响情感变化幅度
    PersonalityProfile --> UrgeEngine : Extraversion 影响枯燥累加速率
    EmotionalState --> UrgeEngine : Arousal/Energy 动态调节触发阈值
```

---

## 6. 多渠道适配器扩展规范与 Better Agent 前端架构

### 6.1 多渠道适配器扩展规范 (Multi-Channel Adapter Pattern)

基于 `BaseAgentComponent` 抽象基类与 NATS 按渠道分级的 Subject 路由机制 (`agent.action.{channel}.{chat_id}`)，新增任意第三方网络 IO 渠道（如 Discord / WhatsApp / QQ Bot）只需实现一个继承类：

```mermaid
classDiagram
    class BaseAgentComponent {
        <<Abstract>>
        +subscribe_to_channel(channel)
        +publish_inbound_message(chat_id, user_id, text, channel)
        +handle_action_decision(payload)*
    }

    class WebGateway {
        +WebSocketConnections map
        +HandleFullDuplexAudioViseme()
    }

    class GotdAdapter {
        +MTProtoTelegramClient
        +HandleTypingHeartbeat()
    }

    class DiscordAdapter {
        <<Future Extension>>
        +DiscordGoClient
        +HandleDiscordEmbeds()
    }

    class WhatsAppAdapter {
        <<Future Extension>>
        +WhatsAppWebBridge
        +HandleMediaAttachments()
    }

    BaseAgentComponent <|-- WebGateway : channel="web"
    BaseAgentComponent <|-- GotdAdapter : channel="telegram"
    BaseAgentComponent <|-- DiscordAdapter : channel="discord"
    BaseAgentComponent <|-- WhatsAppAdapter : channel="whatsapp"
```

* **统一路由控制**：所有适配器仅需向 NATS 发布 `agent.inbound_message` 并附带自身的 `source_channel`（如 `"web"` / `"telegram"` / `"discord"`）。
* **隔离监听**：适配器仅订阅 `agent.action.{channel}.*` 主题，保证各渠道消息下发相互隔离，无需在 Payload 内部手写条件过滤。

---

### 6.2 Better Agent 前端数字人渲染与状态控制架构 (Better Agent Frontend Pipeline)

前端采用 Vue 3 + Vite + Pinia + UnoCSS 构筑（`frontend/apps/stage-web` 与 `@proj-airi/stage-ui`），实现了渲染层与网络状态解耦的响应式管道：

```mermaid
graph LR
    subgraph Frontend_App["frontend/apps/stage-web"]
        WSBridge["betteragent-ws.ts (WebSocket 桥接器)"]
        
        subgraph Pinia_Stores["Pinia State Management"]
            StreamStore["stream-store.ts (文本/音频/Viseme 缓冲帧)"]
            STS2Store["sts2-game-state.ts (杀戮尖塔 2 实时 HUD)"]
            EmotionStore["betteragent-gateway.ts (VAD 3D 情绪/生理指标)"]
            PersonaStore["persona.ts (人设与提示词动态编译 Store)"]
        end

        subgraph Control_Panels["Web UI Control & Overlay"]
            PersonaControl["/settings/persona (4-Tab 人设与边界控制台)"]
            EmotionHUD["EmotionHUDWidget.vue (3D 情绪 & 生理 HUD)"]
        end

        subgraph Render_Engines["Stage UI Engine Canvas"]
            Live2DCanvas["pixi-live2d-display (Live2D 模型渲染器)"]
            WLipSync["wLipSync AudioWorkletNode (元音共振峰实时分析, fallback)"]
            VisemeSchedule["Viseme 调度表 (Stage.vue, MVP)"]
            AudioPlayback["Web AudioContext (PCM 无缝子块播放器)"]
        end
    end

    WSBridge --"JSON Frame (文本/游戏状态)"--> StreamStore
    WSBridge --"Game State Update"--> STS2Store
    StreamStore --"Text Delta"--> ChatUI["聊天窗口 UI"]
    WSBridge -."audio_base64 + visemes (直接轮询消费, 不经过 Store)".-> AudioPlayback
    AudioPlayback --"实时播放"--> WLipSync
    WSBridge -."visemes 时间轴".-> VisemeSchedule
    VisemeSchedule --"ParamMouthOpenY (优先)"--> Live2DCanvas
    WLipSync --"ParamMouthOpenY (无 viseme 数据时的 fallback)"--> Live2DCanvas
    EmotionStore --"Expression & Motion"--> Live2DCanvas
```

> **口型驱动机制**：`Stage.vue` 直接轮询 `window.__betterAgentWSBridge`（不经过 `StreamStore`/Pinia）消费 BetterAgent 的音频与 viseme 数据。JSON `agent.audio_chunk` 消息里的 `visemes` 时间轴会与紧随其后的二进制 AUDI 音频帧配对，按 `audioContext` 播放时钟换算成绝对时间调度表，驱动 Live2D 参数 `ParamMouthOpenY`（`getBetterAgentMouthOpenness`，离散口型→张合度的映射是 MVP 阶段的粗略估计，待真实效果测试后调整）；仅当本次会话尚未收到过任何 viseme 数据时，才回退到 `@proj-airi/model-driver-lipsync` 的 wLipSync `AudioWorkletNode`（对播放中音频做元音共振峰实时分析）驱动同一参数——这是 wLipSync 一直以来的工作方式，此前被误记为"音频频谱分析"，现已订正。

WebGateway 下行 JSON 帧还包括 `agent.stt_transcript`（`{ text, is_final, chat_id }`），
用于把 STT 识别结果回显给前端：`is_final:true` 是 `agent.stt.stream_final` 转发，让语音输入在聊天窗口中显示为普通用户消息；`is_final:false` 是 `agent.stt.stream_partial` 转发的边说边出的中间转写结果，目前只更新 `betteragent-gateway.ts` store 里的 `partialTranscript` 响应式状态，尚无 UI 组件消费。
STT 识别本身仍通过 `agent.inbound_message` 进入同一推理链路（只有 final 结果会触发）。

---

## 7. 团队 Python 微服务扩展矩阵 (Python Microservice Subsystem)

为了在大型项目中保证跨组员协作互不干扰，Python 服务层拆分为**核心认知与记忆引擎**与**团队独立微服务**：

| 微服务名称 | 目录路径 | 监听端口 | 负责人 | 状态 | 核心路由 / 职责 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Cognitive Service** | `services/cognitive/` | `:8091` / `:8092` | 核心 / 褚裕禄 | ✅ 就绪 | LLM 推理、Tool 调度、TTS/STT 流式生成 |
| **Memory Service** | `services/memory/` | — (Redis/Qdrant) | 核心 / 褚裕禄 | ✅ 就绪 | Redis 短时缓存、Qdrant 向量检索、UserProfile 画像 |
| **Game Watcher Service**| `services/game_watcher/`| — (Polling) | 核心 / 褚裕禄 | ✅ 就绪 | Slay the Spire 2 游戏轮询与自动回合触发 |
| **Campus KB Service** | `services/campus_kb/` | `:8093` (HTTP) | 冯文哲 | ✅ 就绪 | `POST /api/kb/ingest`（向量入库）、`POST /api/kb/search`（语义检索） |
| **Admin Backend** | `admin/backend/` | `:8094` (REST) | 谢自立 | ✅ 就绪 | 人设 CRUD、用户管理、会话查看、记忆管理、日程/KB 代理、Config PATCH（共 27 路由） |
| **Admin Frontend** | `admin/frontend/` | `:8095` (Web Dev)| 谢自立 | ✅ 就绪 | Vue 3 + Element Plus 独立后台管理界面（`dist/` 已构建） |
| **Companion Service** | `services/companion/` | `:8096` (HTTP) | 张劭哲 | ✅ 就绪 | 日程 CRUD、`POST /api/companion/query`（NL2SQL）、`GET /api/companion/recommendations`（任务推荐） |

### 7.1 Campus KB 校园知识库交互数据流 (Campus KB RAG Flow)

猫娘（BetterAgent）与校园知识库 `services/campus_kb` 的交互采用**预注入 RAG 上下文**与**自主 Tool Calling**双通道机制：

```mermaid
sequenceDiagram
    participant User as 用户消息
    participant Mem as MemoryHub (:8090)
    participant KB as Campus KB (:8093)
    participant Cog as CognitiveEngine / LLM
    
    rect rgb(240, 248, 255)
    note right of Mem: 通道 1: 预注入上下文 (Passive RAG)
    User->>Mem: NATS (enrich_context_req)
    Mem->>KB: HTTP POST /api/kb/search (query)
    KB-->>Mem: 返回匹配知识条目 (kb_facts)
    Mem->>Cog: NATS (reasoning_req + kb_facts)
    Cog->>Cog: PromptBuilder 注入 [校园知识库] 到 System Prompt
    end

    rect rgb(255, 245, 238)
    note right of Cog: 通道 2: 自主工具调用 (Autonomous Tool Calling)
    Cog->>Cog: LLM 判断预注入上下文不足，触发 tool_call: search_campus_kb
    Cog->>KB: CampusKBTool 发起 HTTP POST /api/kb/search
    KB-->>Cog: 返回精确检索结果 (facts)
    Cog->>Cog: LLM 结合 Tool Message 二次推理
    end
    
    Cog-->>User: 生成包含猫娘口吻与校园知识的最终回复
```

---

## 8. Admin Panel 操作时序 (Admin Panel Interaction Sequence)

B 端管理控制台（`admin/frontend/` → `admin/backend/:8094`）与核心存储层的交互时序，覆盖人设 PATCH 热重载、会话查看与知识库管理三条关键路径：

```mermaid
sequenceDiagram
    autonumber
    actor Admin as 管理员 (Admin Web :8095)
    participant AR as Admin REST (:8094)
    participant YAML as Persona YAML 磁盘
    participant NATS as NATS Message Bus
    participant PL as PersonaLoader (Python In-Memory)
    participant Redis as Redis (短时记忆)
    participant Qdrant as Qdrant (长期记忆)
    participant KB as Campus KB (:8093)
    participant Comp as Companion (:8096)

    rect rgb(240, 248, 255)
    note right of Admin: 路径 1：人设 PATCH 热重载
    Admin->>AR: PATCH /api/admin/personas/{id} (字段白名单: name/appearance/base_prompt...)
    AR->>YAML: ruamel 原子写入 (临时文件+fsync+os.replace, 保留注释与格式)
    AR->>NATS: 直接 Publish "agent.persona.update" (Admin 后端用 nats-py 直连总线, 不经过 Go Core 这一跳)
    NATS-->>PL: Notify "agent.persona.update"
    PL->>PL: 失效 TTL 缓存，重新从磁盘加载 YAML
    AR-->>Admin: 200 OK {id, name, updated_fields, hot_reload: "ok"|"failed"} (NATS 推送失败时 hot_reload 如实报告 failed，不静默吞掉)
    end

    rect rgb(255, 245, 238)
    note right of Admin: 路径 2：会话记录查看 (Redis)
    Admin->>AR: GET /api/admin/sessions?chat_id={id}&limit=50
    AR->>Redis: LRANGE short_term:{chat_id} 0 49
    Redis-->>AR: 返回 JSON 对话历史条目
    AR-->>Admin: 200 OK [{role, content, timestamp}...]
    end

    rect rgb(240, 255, 240)
    note right of Admin: 路径 3：长期记忆管理 (Qdrant)
    Admin->>AR: GET /api/admin/memory/long-term?chat_id={id}
    AR->>Qdrant: REST GET /collections/betteragent_memories/points/scroll
    Qdrant-->>AR: 返回向量记忆点列表
    AR-->>Admin: 200 OK [{id, payload, score}...]
    Admin->>AR: DELETE /api/admin/memory/long-term/{point_id}
    AR->>Qdrant: DELETE /points (point_id)
    AR-->>Admin: 200 OK
    end

    rect rgb(255, 248, 220)
    note right of Admin: 路径 4：知识库管理 (Campus KB 代理)
    Admin->>AR: POST /api/admin/kb/ingest {content, metadata}
    AR->>KB: HTTP POST /api/kb/ingest (透传)
    KB-->>AR: 200 OK {ingested_count}
    AR-->>Admin: 200 OK
    end

    rect rgb(248, 240, 255)
    note right of Admin: 路径 5：日程提醒管理 (Companion 代理)
    Admin->>AR: GET /api/admin/schedules?chat_id={id}
    AR->>Comp: HTTP GET /api/schedule/list?chat_id={id}
    Comp-->>AR: 200 OK [{id, title, remind_at}...]
    AR-->>Admin: 200 OK
    Admin->>AR: DELETE /api/admin/schedules/{schedule_id}
    AR->>Comp: HTTP DELETE /api/schedule/{schedule_id}
    AR-->>Admin: 200 OK
    end
```

> **BYOK Provider 配置热更新（`agent.config.reloaded`）现在有真实消费端**：Admin 后端修改 LLM Provider/API Key 配置后会 Publish `agent.config.reloaded`；`services/cognitive/main.py` 订阅该主题后依次调用 `ProviderFactory.invalidate_cache()`（清空缓存的 Provider 实例）与 `CognitiveEngine.refresh_default_provider()`（重新解析默认 Provider 并重建 `self.providers`），使正在运行的 cognitive 服务无需重启即可切换 Provider/Key，与上面路径 1 的人设热更新是同一类"配置变更 → NATS 广播 → 服务内消费"机制。

---

## 9. 防腐化策略 (Documentation Maintenance Rules)

为了保持设计资产永不过期，开发过程中须遵循以下原则：

1. **单一点真相 (Single Source of Truth)**：任何对 `core/internal/schema/payloads.go` 或状态机 `state_machine.go` 的修改，必须同步更新本文件中的 Mermaid 图纸。
2. **Git Hook 校验**：合并 PR 时检测 `docs/ARCHITECTURE.md` 是否同步变更。
3. **AI Prompt 提示**：在向抗重力 Agent 发出架构重构指令时，附带本文件（`docs/ARCHITECTURE.md`）作为上下文基准。
