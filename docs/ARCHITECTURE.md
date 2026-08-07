# BetterAgent (猫娘 Agent) 系统架构与 SRS 规范

本文档为 `BetterAgent` 项目的文本化架构规范与系统需求说明书（SRS）。所有图纸均使用 **Mermaid** 格式描述，可以直接进行 Git 版本控制与 AI Agent 语义解析，防止设计资产“文档腐化”。

---

## 1. 系统总体拓扑架构 (System Topology & Microservices)

针对生产环境的高并发网络 IO 与复杂 LLM 推理需求，系统采用 **Go Core (高性能控制核) + NATS (中枢消息总线) + Python Services (认知与记忆微服务)** 的异构架构。

```mermaid
graph TD
    subgraph External_Adapters["External IO Networks / Clients"]
        TG["Telegram Cloud API"]
        WebClient["Browser Client (stage-web / Live2D / VRM)"]
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
        GoNatsBus["NatsBus Client (Go)"]

        GotdAdapter --> AntiSpam
        GotdAdapter --> HumanEngine
        ClockEngine --> CircadianEvaluator
        CircadianEvaluator --> EmotionEngine
        EmotionEngine --> CSM
        CSM --> GoNatsBus
        GotdAdapter --> GoNatsBus
        WebGateway --> GoNatsBus
    end

    subgraph Message_Broker["Message Infrastructure"]
        NATS["NATS Server (Pub/Sub + JetStream)"]
    end

    subgraph Python_Services["Python Services Layer"]
        PyNatsBus["NatsBus Client (Python)"]
        
        subgraph Cognitive_Service["Cognitive Service"]
            CognitiveEngine["CognitiveEngine (推理逻辑)"]
            PromptBuilder["PromptBuilder (System Prompt组装)"]
            ToolRegistry["ToolRegistry (Tools/MCP决策)"]
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

        PyNatsBus --> CognitiveEngine
        PyNatsBus --> MemoryHub
    end

    TG <--> GotdAdapter
    WebClient <-->|"WebSocket (全双工)"| WebGateway
    GoNatsBus <--> NATS
    PyNatsBus <--> NATS
```

---

## 2. 消息处理与推理时序 (Sequence Diagram)

用户在 Telegram 发送消息后，Go Core、NATS、Memory Service 与 Cognitive Service 之间的完整异步 Pub/Sub 交互时序：

```mermaid
sequenceDiagram
    autonumber
    actor User as Telegram User
    participant Adapter as GotdAdapter (Go)
    participant NATS as NATS Message Bus
    participant CSM as CentralStateMachine (Go)
    participant Memory as MemoryHub (Python)
    participant Cog as CognitiveEngine (Python)
    participant LLM as LLM Provider

    User->>Adapter: 发送消息 / 媒体文件
    Note over Adapter: AntiSpamGuard 校验通过
    Adapter->>Adapter: 开启 Typing 心跳后台协程
    Adapter->>NATS: Publish "agent.inbound_message" (InboundMessagePayload)
    
    NATS-->>CSM: Notify "agent.inbound_message"
    CSM->>CSM: 状态切换 TransitionTo(THINKING)
    CSM->>NATS: Publish "agent.enrich_context_req" (EnrichContextReqPayload)
    
    NATS-->>Memory: Notify "agent.enrich_context_req"
    Memory->>Memory: 检索短时对话 + Qdrant向量记忆 + UserProfile事实
    Memory->>Memory: TokenBudget 剪裁
    Memory->>NATS: Publish "agent.reasoning_request" (ReasoningRequestPayload)

    NATS-->>Cog: Notify "agent.reasoning_request"
    Cog->>Cog: PromptBuilder 组装带情绪与生理状态的 Prompt
    Cog->>LLM: POST /chat/completions (含 Tool Schema)
    LLM-->>Cog: 返回文本 / Tool Calls (TTS / 画图)
    
    opt 触发 Tool Calls (如生图/语音生成)
        Cog->>Cog: 执行工具生成图片/音频文件
    end

    Cog->>NATS: Publish "agent.action_decision" (ActionDecisionPayload)
    
    NATS-->>Adapter: Notify "agent.action_decision"
    Adapter->>Adapter: 停止 Typing 心跳协程
    Adapter->>Adapter: HumanizationEngine 模拟拟打字延迟 (min 1.5s, max 8.0s)
    Adapter->>User: 发送回复文本 / 音频 / 图片
    Adapter->>NATS: Publish "agent.action_completed" (ActionCompletedPayload)

    NATS-->>Memory: Notify "agent.action_completed" (追加短时对话历史)
    NATS-->>CSM: Notify "agent.action_completed" (状态切换 TransitionTo IDLE)
```

---

## 3. 按 Chat 隔离状态机 (Per-Chat State Machine & Watchdog)

系统由 Go 实现的 `PerChatStateMachineManager` (`CentralStateMachine`) 掌控，通过 `map[int64]*ChatStateMachine` + `sync.RWMutex` 实现按 Telegram `chatID` 严格的并发隔离与状态生命周期管理，并配备 **Deadman Switch Watchdog（超时自愈死人开关）**：

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

    INTERRUPTED --> CANCELLING : CANCEL_IN_FLIGHT_REQUESTS
    CANCELLING --> THINKING : RE_REASONING (带着最新打断输入重新思考)
    CANCELLING --> IDLE : PURGE_RESET

    EXECUTING_ACTION --> IDLE : ACTION_COMPLETED
    EXECUTING_ACTION --> ERROR_RECOVERY : TELEGRAM_API_ERROR 或 DEADMAN_TIMEOUT [>45s]

    IDLE --> SLEEPING : TICK [is_sleep_time == True] 或 GOODNIGHT_EVENT
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
| **基础中枢** | `agent.tick` / `agent.inbound_message` / `agent.error` | 系统心跳 / 上行消息 / 错误告警 |
| **记忆与推理** | `agent.enrich_context_req` / `agent.reasoning_request` | 记忆检索请求 / LLM 推理触发 |
| **决策与执行** | `agent.action_decision` / `agent.action_completed` | 行动决策下发 / 行动完成确认 |
| **VAD 听觉** | `agent.speech.start` / `agent.speech.end` | 麦克风开始/结束说话 |
| **打断撤销** | `agent.user.interrupt` / `agent.stream.cancel_req` | 用户打断 (Barge-in) 撤销 |
| **数字人流** | `agent.audio.chunk` / `agent.viseme.data` / `agent.tts.stream_chunk` | TTS 音频切片 / Viseme 口型 |
| **流式控制** | `agent.stt.stream_chunk` / `agent.stt.stream_final` / `agent.tts.stream_end` | STT 转写流 / TTS 结束 |
| **流式状态** | `agent.stream.cancel_ack` / `agent.stream.state_change` | 流式撤销确认 / 状态变更广播 |
| **视觉与感知** | `agent.vision.frame` / `agent.emotion.update` | 画面快照 / 情绪动作更新 |

### 4.2 Payload 类图 (Class Diagram)

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
        +int message_id
        +String source_channel
        +String raw_text
        +String file_path
        +int reply_to_message_id
        +String media_type
        +String voice_transcript
        +String chat_type
        +String sender_username
        +String sender_display_name
    }

    class VisionFramePayload {
        +int64 chat_id
        +String image_base64
        +String format
        +String source_type
    }

    class ReasoningRequestPayload {
        +int64 chat_id
        +int64 user_id
        +String system_prompt_override
        +List~Dict~ short_term_history
        +Dict user_profile
        +List~String~ rag_facts
        +float mood_score
        +String formatted_time_str
        +InboundMessagePayload inbound_message
    }

    class ActionDecisionPayload {
        +int64 chat_id
        +String source_channel
        +String action_type
        +String text_content
        +float typing_delay
        +String media_type
        +int reply_to_message_id
        +String voice_path
        +String photo_path
        +String sticker_id
        +String reaction_emoji
    }

    class ActionCompletedPayload {
        +int64 chat_id
        +int sent_message_id
        +ActionDecisionPayload action_decision
        +String status
        +float sent_time
        +String error_detail
    }

    class TickPayload {
        +String iso_time
        +String time_of_day
        +float idle_duration_seconds
        +bool is_sleep_hours
        +int tick_counter
        +String emotion_description
    }

    EventEnvelope --> BasePayload
    BasePayload <|-- InboundMessagePayload
    BasePayload <|-- ReasoningRequestPayload
    BasePayload <|-- ActionDecisionPayload
    BasePayload <|-- ActionCompletedPayload
    BasePayload <|-- TickPayload
```

---

## 5. 猫娘“情感与生理”机制 (Affective Computing Engine)

驻留在 Go Core 内部的极速心理与生理计算模型：

```mermaid
classDiagram
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
        +bool IsJealous
        +float BaselineValence
        +Time LastUpdated
        +ApplySentimentDelta(delta_v, delta_a, delta_aff)
        +ApplyTimeDecay(elapsed_seconds)
        +ToPromptDescription() String
    }

    class PersonalityProfile {
        +float TsundereLevel
        +float Clinginess
        +float JealousyThreshold
        +float CatNature
        +float Neuroticism
        +float Extraversion
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

    CircadianRhythmEvaluator --> EmotionalState : 驱动昼夜衰减
    PersonalityProfile --> EmotionalState : 影响情感变化幅度
```

---

## 6. 防腐化策略 (Documentation Maintenance Rules)

为了保持设计资产永不过期，开发过程中须遵循以下原则：

1. **单一点真相 (Single Source of Truth)**：任何对 `core/internal/schema/payloads.go` 或状态机 `state_machine.go` 的修改，必须同步更新本文件中的 Mermaid 图纸。
2. **Git Hook 校验**：合并 PR 时检测 `docs/ARCHITECTURE.md` 是否同步变更。
3. **AI Prompt 提示**：在向抗重力 Agent 发出架构重构指令时，附带本文件（`docs/ARCHITECTURE.md`）作为上下文基准。
