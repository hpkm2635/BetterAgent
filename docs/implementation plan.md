# 实现计划：方案 C - text_delta 驱动的流式字幕（Neuro-sama 式打字机效果）

> 本文件取代原「方案 B - 前端 Audio 驱动卡拉 OK 逐字高亮」。方案 B 在设计评审中发现一个非边角情况的漏洞：`spokenRatio` 是按**单个 TTS 音频块**（一句话）的播放进度算的，但要高亮的 `lastNonEmptyText`（`live-caption-overlay.vue`）是 `streamStore.streamingMessage.content` —— **整轮回复累积的全部文字**，可能横跨多句、多个独立音频块。只要一轮回复超过一句话（常态而非特例），"用单句播放比例去算整段累积文字的高亮位置"这个映射从一开始就是错的。即便只看单句内部，线性字符比例也不是真正的音素时长（标点、儿化音、数字读法都会让每字实际时长不均），"精准"这个说法站不住。
>
> 本方案改用更朴素、也更不容易穿帮的思路：不去反推"现在念到第几个字"，而是让字幕跟着文字本来就在流式到达的节奏**逐步显示出来**——类似 Neuro-sama 的字幕效果：文字随着她说话逐渐"冒出来"，观感自然，且没有一个"这个字该不该被点亮"的硬性时间承诺会被违反。

## 用户审查点 (User Review Required)

> [!NOTE]
> - **按 chunk 而非按整轮流式**：现有 `text_delta` 是按句（TTS chunk）到达的（见 `core/internal/webgateway/nats_bridge.go` 的 `handleActionDecisionMsg` / `services/tts/main.py` 的 `is_sentence_start`），不是逐字/逐 token。整句一次性到达就整句一次性显示，不会有"打字机"的观感——所以打字机节奏需要在**前端**、以**每个 chunk 自己的音频时长**为基准来播出该 chunk 的文字，而不是等它整句一次性 setState。
> - **不复用整段累积文字做比例映射**：每个音频 chunk 只负责揭示**它自己**携带的那段文字（`text_delta`），播完即视为"已完全揭示"并并入永久字幕；下一个 chunk 从头开始揭示自己的文字。彻底绕开方案 B 的多 chunk 拼接错位问题。
> - **复用已有的 viseme 时间轴，而不是新造一套比例计算**：`services/tts/viseme_generator.py` 的 `text_to_visemes(text, duration_sec)` 已经把文本按发音特征均匀分布出了一份 `[{time_offset, viseme_id, shape}, ...]` 时间轴，且已经通过 `agent.audio.chunk` → Go `handleAudioChunkMsg` 转发进了浏览器收到的 JSON `agent.audio_chunk` 消息（当前被 `betteragent-ws.ts` 直接丢弃，见另一份关于 Viseme 管线的讨论）。这份时间轴的时间粒度就是"这句话有几个发音单元、大致落在这句音频的第几秒"，天然适合同时驱动口型**和**打字机节奏——用它当字幕揭示的节拍源，比另外用 `audioContext.currentTime` 算一遍比例更省一套逻辑，也不会让两套时间基准（口型用 viseme、字幕用音频比例）互相打架。没有 viseme 数据时（比如某个 chunk 生成失败），退化为"按字符数 ÷ chunk 时长"匀速揭示，不阻塞显示。
> - **和 Viseme 管线是同一个前置依赖**：本方案要求前端先能解析 JSON `agent.audio_chunk` 消息、并把某个 chunk 的 visemes/文本和它对应的**实际播放起止时间**（`betterAgentNextChunkStartTime` / `audioBuffer.duration`）关联起来——这正是 Viseme 管线本身需要打通的部分。建议两者一起规划实现，不要分两次各自重新做一遍"chunk 播放时间线"的追踪。

---

## 拟修改与新增文件 (Proposed Changes)

### 1. 前端状态与音效控制层 (Stage & Gateway Store)

#### [MODIFY] [betteragent-ws.ts](file:///d:/projects/BetterAgent/frontend/packages/stage-ui/src/services/betteragent-ws.ts)
- 停止丢弃 `agent.audio_chunk` JSON 消息，解析出 `visemes` 与该 chunk 自身的 `text_delta`（如果有），随音频回调一起传给订阅者（`AudioChunkCallback` 需要扩展）。

#### [MODIFY] [betteragent-gateway.ts](file:///d:/projects/BetterAgent/frontend/packages/stage-ui/src/stores/modules/betteragent-gateway.ts)
- 新增 `revealedCaption`（已揭示、可直接渲染的字幕文本）与 `pendingChunkText` / `pendingChunkVisemes`（当前正在播放的 chunk 及其节拍源）响应式状态。
- 提供 `beginChunkReveal(text, visemes, durationSec)`：chunk 开始播放时调用，登记这个 chunk 要揭示的文字与节拍源。
- 提供 `advanceReveal(playbackElapsedSec)`：由 `Stage.vue` 的播放进度回调驱动，按 elapsed 时间在 viseme 时间轴（或退化的匀速节拍）上找到"此刻应该揭示到第几个字"，更新 `revealedCaption`。
- chunk 播放结束时，把这个 chunk 的文字整体并入永久字幕缓冲，清空 `pendingChunkText`，等待下一个 chunk。

#### [MODIFY] [Stage.vue](file:///d:/projects/BetterAgent/frontend/packages/stage-ui/src/components/scenes/Stage.vue)
- 在 `playBetterAgentAudioChunk` 里，播放开始时调用 `beginChunkReveal(...)`；已有的 `requestAnimationFrame` 口型驱动循环里顺带调用 `advanceReveal(...)`（同一个 rAF 循环，不新开一个），避免口型和字幕节拍来自两个不同步的计时器。
- 音频播放中断/取消（barge-in）时同步清空 `pendingChunkText`，避免残留没播完的字幕。

### 2. 前端 UI 实时字幕层 (Gadgets)

#### [MODIFY] [live-caption-overlay.vue](file:///d:/projects/BetterAgent/frontend/packages/stage-ui/src/components/gadgets/live-caption-overlay.vue)
- 显示内容从 `lastNonEmptyText`（整轮累积文字，一次性渲染）改为 `revealedCaption + pendingChunkText 中已揭示的前缀`。
- 不再需要按字符索引做高亮着色（那是方案 B 的效果，已放弃）；正常渲染已揭示的文字即可，未揭示部分不显示（而不是半透明显示已知全文——避免"提前剧透"猫娘还没说出口的话）。

---

## 验证计划 (Verification Plan)

### 自动化与类型校验
- 运行 `pnpm -F @proj-airi/stage-ui typecheck` 验证 TS 类型无误。
- 运行 `go test -race -v ./internal/webgateway/...` 确保后端推包正常（本方案不改动 Go 端逻辑，仅依赖 Viseme 管线打通后已经在转发的数据）。

### 手动验证
- 启动 BetterAgent，分别测试单句短回复、跨多个 TTS chunk 的长回复。
- 观察字幕是否随每个 chunk 的语音播放自然"打出来"，chunk 之间衔接是否顺畅（不应该出现"整句瞬间跳出"或"卡在某个字不动"）。
- 触发 barge-in 打断，确认字幕跟着音频一起停止揭示，不会残留一句"打到一半"的文字继续自己往下跑。
- 对比无 viseme 数据（该 chunk viseme 生成失败）时的匀速退化效果是否依然自然可用。

## 未决问题 (Open Questions，实现前需要拍板)

- Viseme 管线本身还没打通（前端目前整个丢弃 `agent.audio_chunk` JSON 消息）——本方案依赖它，建议合并成一轮实现，而不是先做字幕再回头补 Viseme 消费。
- 是否需要给"揭示中"的最后一个字加一个轻量的光标/淡入效果（纯视觉细节，不影响本方案的时序设计），留给实现时决定。
