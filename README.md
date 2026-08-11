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

## 文档

- [系统架构与 SRS 规范](docs/ARCHITECTURE.md)
- [安全加固记录与部署基线](docs/SECURITY.md) —— 部署前必读，列出了必须设置的鉴权环境变量与检查清单

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
# 必须设置 NATS_USER / NATS_PASSWORD —— NATS 总线承载所有服务间的私密对话数据与
# 控制指令，Go core / Python 微服务 / NATS 服务端三方都要求鉴权，缺一不可，
# 否则任何能连到 4222 端口的人都能伪造消息、读取任意用户的对话记忆。
# 建议用 `openssl rand -hex 24` 生成 NATS_PASSWORD。
#
# 必须设置 WEBGATEWAY_TOKEN —— 保护 8080 端口的数字人 WebSocket 网关（/ws），
# 前端连接时需带上 ?token=<WEBGATEWAY_TOKEN>，否则拒绝握手。
# 可选设置 WEBGATEWAY_ALLOWED_ORIGINS —— 限制哪些浏览器 Origin 能跨域连接
# /ws（逗号分隔的 glob，如 "example.com,*.example.com"）；不设置则不做 Origin
# 校验（本地开发、或前端部署在别的域名时留空即可，token 才是真正的门禁）。
#
# 必须设置 REDIS_PASSWORD —— Redis 里存的是每个用户的短时对话缓冲区，
# 未鉴权会导致任何能连到 6379 端口的人读到所有用户的最近聊天记录。
#
# 必须设置 QDRANT_API_KEY —— 目前 VectorMemoryStore 还是内存 stub，尚未真正
# 连接 Qdrant，但仍建议现在就锁死容器本身，避免以后接入时忘记加鉴权。
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
# 首次运行需要 cp frontend/apps/stage-web/.env.example frontend/apps/stage-web/.env
# 并把 VITE_BETTERAGENT_WS_TOKEN 设成跟根目录 .env 里的 WEBGATEWAY_TOKEN 一样的值，
# 否则 WebGateway 会拒绝握手，前端会无限重连。
cd frontend
pnpm --filter @proj-airi/stage-web dev

```

---

## 鸣谢 / Acknowledgments

* **Airi 项目**：[moeru-ai/airi](https://github.com/moeru-ai/airi)
```