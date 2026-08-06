# BetterAgent (猫娘 Agent)

拥有“猫娘”人格的 Telegram Agent，采用 **Go + NATS + Python 混合架构**。

## 架构组成

- **Go Core (`betteragent-core`)**: `core/`
  - MTProto 通信 (`gotd/td`)
  - 时钟心跳与作息计算 (`ClockEngine`, `CircadianRhythm`)
  - 6 状态状态机 (`CentralStateMachine`)
  - 情绪与性格模型 (`EmotionalState`, `PersonalityProfile`)
  - NATS 消息总线接口 (`NatsBus`)
  - 人性化延迟与防封控制 (`HumanizationEngine`, `AntiSpamGuard`)

- **Python Services**: `services/`
  - `memory-service`: 记忆与检索服务 (`MemoryHub`, `VectorMemoryStore` 艾宾浩斯遗忘曲线, `ShortTermBuffer`, `TokenBudgetManager`)
  - `cognitive-service`: 认知推理与工具调度 (`CognitiveEngine`, `PromptBuilder`, `GeminiProvider`, `ClaudeProvider`, `TTSTool`, `ImageGenTool`, `TelegramActionTool`)

- **Infrastructure**: `deploy/`
  - NATS Server (Pub/Sub + JetStream)
  - Redis (状态锁与缓冲)
  - Qdrant (向量记忆存储)

## 启动指南

1. **启动基础设施**:
   ```bash
   cd deploy
   docker-compose up -d
   ```

2. **启动 Python 计算服务**:
   ```bash
   # Terminal 1: Memory Service
   cd services/memory
   pip install -r requirements.txt
   python main.py

   # Terminal 2: Cognitive Service
   cd services/cognitive
   pip install -r requirements.txt
   python main.py
   ```

3. **启动 Go 核心进程**:
   ```bash
   cd core
   cp ../.env.example .env # 填入 TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE
   go run ./cmd/main.go
   ```

💡 本地常用命令：

# 启动所有服务
.\scripts\win_start.ps1
# 停止所有服务
.\scripts\win_stop.ps1

# 复制脚本后配置 Systemd 保活
sudo cp deploy/betteragent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now betteragent

# 启动前端画面
cd frontend
pnpm --filter @proj-airi/stage-web dev

# 特别致谢 Airi 项目
https://github.com/moeru-ai/airi
