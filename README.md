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
  - `tts-service`: 语音合成服务 (`GPTSoVITSClient`, `CosyVoiceClient`, `AudioNormalizer`)

- **Infrastructure**: `deploy/`
  - NATS Server (Pub/Sub + JetStream)
  - Redis (状态锁与缓冲)
  - Qdrant (向量记忆存储)

---

## 快速启动指南

### 1. 环境准备 (Python 3.12+)

创建标准的虚拟环境并安装统一依赖：

```bash
# 创建虚拟环境 (.venv)
python -m venv .venv

# 激活环境 (Windows)
.\.venv\Scripts\activate
# 激活环境 (Linux/macOS)
source .venv/bin/activate

# 安装项目统一依赖
pip install -r requirements.txt
```

### 2. 拷贝环境配置

```bash
cp .env.example .env
# 编辑 .env 填入 TELEGRAM_API_ID, TELEGRAM_API_HASH 等配置
```

### 3. 一键编排启动（支持 Windows / Linux / macOS）

使用内置的跨平台守护进程 `runner.py`（支持自动拉起微服务、进程健康检查与自动重启）：

```bash
# 直接使用 runner 启动（跨平台通用）
python runner.py

# 或在 Windows 下使用脚本：
.\scripts\win_start.ps1

# 或在 Linux 下使用脚本：
./scripts/linux_start.sh
```

---

## 本地开发常用指令

```bash
# 停止所有微服务
.\scripts\win_stop.ps1

# 启动 Web UI 调试端
cd frontend
pnpm --filter @proj-airi/stage-web dev

# 特别致谢 Airi 项目
https://github.com/moeru-ai/airi
