# 实现计划：方案 B - 前端 Audio 驱动“卡拉 OK 逐字高亮”与精准口型配合

本计划旨在实现音频驱动的高精度字音同步，让前端实时字幕具备 **Karaoke 逐字发光高亮** 效果，并结合 Web Audio API 的实时音频能量实现 Live2D / 3D 数字人口型的毫秒级精准配合。

## 用户审查点 (User Review Required)

> [!NOTE]
> - **逐字高亮平滑度**：通过 Web Audio API `audioContext.currentTime` 与 `requestAnimationFrame` 高频轮询，计算当前发音时间在整句音频中的比例，动态将已发音字符渲染为粉色高亮加粗样式。
> - **口型配合**：利用已有的 `AudioAnalyser` 节点，在音频播放期间将实时 FFT 频域能量直接映射到数字人嘴巴开合度参数（`ParamMouthOpenY`）。

---

## 拟修改与新增文件 (Proposed Changes)

### 1. 前端状态与音效控制层 (Stage & Gateway Store)

#### [MODIFY] [betteragent-gateway.ts](file:///d:/projects/BetterAgent/frontend/packages/stage-ui/src/stores/modules/betteragent-gateway.ts)
- 新增 `spokenRatio`（0.0 ~ 1.0 播放进度比例）与 `activeCharIndex` 状态响应式变量。
- 提供 `updateAudioProgress(ratio: number)` 方法供播放器更新。

#### [MODIFY] [Stage.vue](file:///d:/projects/BetterAgent/frontend/packages/stage-ui/src/components/scenes/Stage.vue)
- 在 `playBetterAgentAudioChunk` 启动播放时，挂载 `requestAnimationFrame` 监听器。
- 计算 `(audioContext.currentTime - startTime) / duration` 动态算得发音进度比例，并实时更新至 `gatewayStore`。
- 音频播放结束或停顿时归零进度。

### 2. 前端 UI 实时字幕层 (Gadgets)

#### [MODIFY] [live-caption-overlay.vue](file:///d:/projects/BetterAgent/frontend/packages/stage-ui/src/components/gadgets/live-caption-overlay.vue)
- 将 `lastNonEmptyText` 拆解为字符数组。
- 根据 `spokenRatio` 实时计算当前高亮到的字符索引 `highlightIndex`。
- 将已播出的字符渲染为 `text-pink-500 font-bold dark:text-pink-400 drop-shadow-sm transition-all duration-150` 绚丽卡拉 OK 高亮特效；未播出的字符保持优雅半透明显示。

---

## 验证计划 (Verification Plan)

### 自动化与类型校验
- 运行 `pnpm -F @proj-airi/stage-ui typecheck` 验证 TS 类型无误。
- 运行 `go test -race -v ./internal/webgateway/...` 确保后端推包正常。

### 手动验证
- 启动 BetterAgent (`win_start.ps1`)，向猫娘发送长短不同的文本。
- 观察前端字幕框是否伴随猫娘的声音**从左至右精准逐字高亮发光**。
- 观察数字人口型是否在声音响起的瞬间精准张合。
