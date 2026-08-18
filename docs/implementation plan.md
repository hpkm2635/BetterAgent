# 人设实时管控面板 + 情绪 HUD — Final Implementation Plan

> **所有开放问题已解决（2026-08-18）**
>
> - **Q1 已确认**：HTTP PATCH（持久化 YAML）与 WebSocket 热刷新（内存即时生效）两者并存。
> - **Q2 已确认**：傲娇/粘人滑块仅在前端拼接进 `base_prompt`，不向 Admin API 发送非白名单字段。
> - **新增 Sprint A-3.5**：情绪状态实时 HUD 显示（对接 Go Core `EmotionalState`）。

---

## 一、技术背景与可复用轮子

### 1. 情感发动机数据模型（Go Core）

[`emotional_state.go`](file:///d:/projects/BetterAgent/core/internal/emotion/emotional_state.go) 定义的 3D VAD 空间 + 生理指标：

| 字段 | 类型 | 范围 | 含义 |
|:---|:---|:---|:---|
| `Valence` | float64 | [-1.0, 1.0] | 愉悦度 |
| `Arousal` | float64 | [0.0, 1.0] | 激动度 |
| `Energy` | float64 | [0.0, 1.0] | 精力值 |
| `SocialBattery` | float64 | [0.0, 1.0] | 社交电量 |
| `AffectionLevel` | float64 | [0.0, 100.0] | 好感度 |
| `CurrentMoodTag` | MoodEnum | HAPPY/NEUTRAL/MOODY/SLEEPY/JEALOUS | 当前心情 |
| `IsJealous` | bool | — | 吃醋状态 |

### 2. 现有 WS 消息体系（可扩展点）

[`protocol.go`](file:///d:/projects/BetterAgent/core/internal/webgateway/protocol.go) 中 `AgentEmotionPayload` 目前只有 `emotion string + action string`。我们在其中扩充结构化数值字段，完全向下兼容。

### 3. Admin 后台白名单（6 字段）

`PATCH http://localhost:8094/api/admin/personas/{id}` 白名单：
`name`, `appearance`, `base_prompt`, `sleepy_prompt`, `knowledge_scope`, `forbidden_topics`

前端提交时**严格限制在白名单范围内**，傲娇/粘人数值通过前端编译注入 `base_prompt`，不单独发送。

---

## 二、整体架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│ AIRI stage-web 设置面板 (:5173)                                      │
│                                                                     │
│  [人设与边界] 设置页                       [情绪 HUD 开关]           │
│  ├── BasicProfileTab.vue  →  name, userCallsign, catchphrases       │
│  ├── PromptEditorTab.vue  →  base_prompt, sleepy_prompt             │
│  ├── BoundaryTab.vue      →  knowledge_scope, forbidden_topics      │
│  ├── EmotionWeightTab.vue →  tsundereWeight/clingyWeight → 编译     │
│  └── 保存时:                                                         │
│      1. 前端将滑块值编译注入 base_prompt                             │
│      2. 发 PATCH localhost:8094/api/admin/personas/catgirl           │
│      3. 同时发 WS: type=admin.persona_update 触发内存热刷新          │
│                                                                     │
│  EmotionHUDWidget.vue (浮动 HUD, 可开关)                            │
│  └── 订阅 WS: type=agent.emotion (带完整 EmotionalState 数值)        │
└───────┬──────────────────────────────────────────┬──────────────────┘
        │ HTTP REST (:8094)                         │ WebSocket (:8080)
        ▼                                           ▼
  Admin 后端 (谢自立)                    Go WebGateway
  PATCH personas/catgirl                 ├── 【扩展】agent.emotion 广播
  ruamel.yaml 原地更新                   │   AgentEmotionPayload 包含完整数值
  config/persona/catgirl.yaml            │
                                         └── 【新增】处理 admin.persona_update
                                             发布 NATS agent.persona.update
                                                      ↓
                                             Python PersonaLoader
                                             NATS 订阅句柄
                                             ruamel.yaml 热更新 + invalidate_cache()
```

---

## 三、详细改动清单

---

### Layer 0: 类型扩展

#### [MODIFY] `packages/stage-ui/src/types/airiCard.ts`

在 `AiriExtension.modules` 新增 `persona` 扩展字段（**仅本地持久化，不发往 Admin API**）：

```ts
persona?: {
  userCallsign?: string       // 称呼用户的词，如 "主人" / "学长" / "哥哥"
  catchphrases?: string[]     // 语气词池，如 ["喵~", "呜咪~", "哼"]
  tsundereWeight?: number     // 0–100，傲娇比例
  clingyWeight?: number       // 0–100，粘人比例
  campusKbEnabled?: boolean   // Campus KB 开关
  maxReplyLength?: number     // 单次回复最大字符数
}
```

---

### Layer 1: Persona API 服务（Admin 后台对接 + 降级）

#### [NEW] `packages/stage-ui/src/services/persona-api.ts`

```ts
const ADMIN_API_BASE = import.meta.env.VITE_ADMIN_API_BASE ?? 'http://localhost:8094'

/** 白名单字段，严格匹配 API-CONTRACT.md §2.1 */
export interface PersonaPatch {
  name?: string
  appearance?: string
  base_prompt?: string
  sleepy_prompt?: string
  knowledge_scope?: string
  forbidden_topics?: string
}

export async function fetchPersona(id: string): Promise<PersonaPatch | null>
export async function patchPersona(id: string, patch: PersonaPatch): Promise<boolean>
```

- `fetchPersona`: `GET /api/admin/personas/{id}`, 失败返回 `null`（降级路径）。
- `patchPersona`: `PATCH /api/admin/personas/{id}`, 失败返回 `false`，由 store 处理本地缓存。
- 降级时 toast 提示：`"Admin 服务 (8094) 未连通，修改已在本地生效"`。

---

### Layer 2: Persona Pinia Store

#### [NEW] `packages/stage-ui/src/stores/persona.ts`

```ts
export const usePersonaStore = defineStore('betteragent-persona', () => {
  const personaId = ref('catgirl')
  // 本地草稿（AiriCard 扩展字段，含滑块值等非白名单项）
  const localDraft = useLocalStorageManualReset<AiriExtension['modules']['persona']>(
    'ba-persona-draft', {}
  )
  // 远端当前值（从 Admin 后台 fetch）
  const remotePersona = ref<PersonaPatch | null>(null)
  const isSynced = ref(false)
  const lastSyncAt = ref<number | null>(null)

  // 合并视图：远端优先于 YAML 默认值，本地草稿优先于远端
  const merged = computed(() => ({ ...remotePersona.value, ...localDraft.value }))

  async function fetchRemote(): Promise<void>

  /**
   * 保存人设到 Admin 后台（HTTP PATCH）并触发 WS 热刷新。
   * 傲娇/粘人等本地字段先编译注入 base_prompt，再提交白名单字段。
   */
  async function save(patch: PersonaPatch, localOverrides?: AiriExtension['modules']['persona']): Promise<void>

  function discardDraft(): void

  return { personaId, merged, isSynced, lastSyncAt, fetchRemote, save, discardDraft }
})
```

**"编译注入"逻辑**（`buildBasePrompt(patch, overrides)`）：

```ts
function buildBasePrompt(base: string, overrides: AiriExtension['modules']['persona']): string {
  const lines: string[] = []
  if (overrides?.userCallsign)
    lines.push(`【用户称呼】：${overrides.userCallsign}`)
  if (overrides?.catchphrases?.length)
    lines.push(`【语气助词】：${overrides.catchphrases.join('、')}`)
  if (overrides?.tsundereWeight !== undefined)
    lines.push(`【傲娇权重】：${overrides.tsundereWeight}%`)
  if (overrides?.clingyWeight !== undefined)
    lines.push(`【粘人权重】：${overrides.clingyWeight}%`)
  const header = lines.length ? `${lines.join('\n')}\n\n` : ''
  return header + base
}
```

---

### Layer 3: WebSocket Bridge 扩展（双向）

#### [MODIFY] `packages/stage-ui/src/services/betteragent-ws.ts`

**新增发送方法（前端 → Go WebGateway，热刷新通道）**：

```ts
/** 触发 Go WebGateway 将 persona patch 发布到 NATS，Python PersonaLoader 热更新内存。 */
public sendPersonaUpdate(personaId: string, patch: PersonaPatch): void {
  this.sendJson({ type: 'admin.persona_update', payload: { persona_id: personaId, ...patch } })
}
```

**新增情绪状态 listener 类型（Go → 前端，情绪 HUD 数据源）**：

```ts
export interface EmotionalStatePayload {
  mood: 'HAPPY' | 'NEUTRAL' | 'MOODY' | 'SLEEPY' | 'JEALOUS'
  valence: number       // [-1.0, 1.0]
  arousal: number       // [0.0, 1.0]
  energy: number        // [0.0, 1.0]
  social_battery: number // [0.0, 1.0]
  affection: number     // [0.0, 100.0]
  is_jealous: boolean
  description: string   // ToPromptDescription() 原始文本
}

export type EmotionStateCallback = (state: EmotionalStatePayload, action?: string) => void

public onEmotionState(cb: EmotionStateCallback): () => void
```

`handleTextFrame` 内 `agent.emotion` case 同时解析新增字段，向下兼容旧的 `emotion string` 格式：

```ts
case 'agent.emotion': {
  const p = msg.payload
  const legacyEmotion = typeof p === 'string' ? p : (p?.emotion ?? '')
  this.emotionListeners.forEach(cb => cb(legacyEmotion, p?.action))
  // 新：带数值 payload 时额外触发 emotionStateListeners
  if (p && typeof p === 'object' && 'valence' in p)
    this.emotionStateListeners.forEach(cb => cb(p as EmotionalStatePayload, p.action))
  break
}
```

#### [MODIFY] `packages/stage-ui/src/stores/modules/betteragent-gateway.ts`

新增 `emotionalState` 响应式对象并订阅：

```ts
const emotionalState = ref<EmotionalStatePayload | null>(null)

// 在 initialize() 内：
betterAgentWSBridge.onEmotionState((state) => {
  emotionalState.value = state
})

// 暴露：
return { ..., emotionalState }
```

---

### Layer 4: 前端 Vue 页面与组件

#### [NEW] `apps/stage-web/src/pages/settings/persona/index.vue`

路由入口，基于 `reka-ui` `TabsRoot` 实现四 Tab 布局：

```
[基础人设] [提示词编辑] [知识边界] [情感权重]
```

页头包含：
- 人设选择器（目前固定 `catgirl`，为多人设做预留）
- 「从后台拉取」按钮（触发 `personaStore.fetchRemote()`）
- 同步状态 Badge（`isSynced` + `lastSyncAt`）

---

#### [NEW] `BasicProfileTab.vue`

字段 | UI 组件 | 对应后端字段
:---|:---|:---
角色名称 | `FieldInput` | `name`（Admin 白名单）
用户称呼 | `FieldInput` placeholder: "主人" | `localDraft.userCallsign`（本地）
语气词池 | Tag Input（自定义） | `localDraft.catchphrases`（本地）

---

#### [NEW] `PromptEditorTab.vue`

- `base_prompt` 编辑区：`<textarea>` + 字符计数 + diff 预览（before/after 对比）。
- `sleepy_prompt` 编辑区：折叠 `<details>` 面板。
- 实时预览：调用 `buildBasePrompt(base, localDraft)` 展示编译后的完整 Prompt。
- 保存操作：同时执行：
  1. `patchPersona('catgirl', compiledPatch)` → Admin HTTP PATCH（持久化 YAML）
  2. `betterAgentWSBridge.sendPersonaUpdate('catgirl', compiledPatch)` → WS 热刷新（即时生效）

---

#### [NEW] `BoundaryTab.vue`

字段 | UI 组件 | 对应后端字段
:---|:---|:---
知识范围 | `<textarea>` | `knowledge_scope`（Admin 白名单）
禁忌话题 | Tag Input | `forbidden_topics`（Admin 白名单）
Campus KB | Toggle Switch | `localDraft.campusKbEnabled`（本地 AiriCard）
回复长度上限 | Slider + 数字输入 | `localDraft.maxReplyLength`（本地 AiriCard）

---

#### [NEW] `EmotionWeightTab.vue`

| 控件 | 含义 | 本地字段 |
|:---|:---|:---|
| 傲娇系数 Slider (0–100) | 傲娇语气比例 | `tsundereWeight` |
| 粘人系数 Slider (0–100) | 粘人语气比例 | `clingyWeight` |
| 实时预览 | 编译后拼接片段展示 | — |

> 保存时不向 Admin API 单独发送这两个字段，而是注入编译到 `base_prompt` 文本头部后一并提交。

---

#### [NEW] `components/EmotionHUDWidget.vue`（浮动情绪 HUD）

**可选开关按钮**（集成在 Stage.vue 右侧工具栏，图标 `i-solar:heart-pulse-bold`）。开启后在舞台右上角显示毛玻璃 HUD 卡片：

```
┌─────────────────────────────┐
│ 💖 Camelia 当前状态          │
│ 😊 HAPPY                    │
│ ❤️ 好感度  ████████░░  85   │
│ ⚡ 精力    ██████░░░░  78%  │
│ 🔋 社交电量 ████████░░  90% │
│ ─────────────────────────── │
│ 愉悦度 +0.65  激动度  0.50  │
│ 🔴 吃醋状态：否             │
└─────────────────────────────┘
```

- 数据源：`useBetterAgentGatewayStore().emotionalState`
- 更新频率：每次 `agent.emotion` WS 消息即时刷新（无轮询）
- 动画：`AffectionLevel` 变化时进度条带平滑 CSS `transition`；`IsJealous` 触发时 badge 红色闪烁

---

#### [MODIFY] 设置入口（Settings 列表页）

在现有设置卡片列表中新增入口项：

```ts
{ 
  title: '人设与边界',
  description: '调整猫娘名字、语气词、知识边界与提示词',
  icon: 'i-solar:cat-bold',
  route: '/settings/persona'
}
```

---

### Layer 5: Go WebGateway 扩展（最小改动）

#### [MODIFY] `core/internal/webgateway/protocol.go`

**扩充 `AgentEmotionPayload`，向下兼容旧格式**：

```go
// AgentEmotionPayload 向下兼容旧的 emotion string，同时携带完整数值供前端情绪 HUD 使用。
type AgentEmotionPayload struct {
    Emotion       string  `json:"emotion"`
    Action        string  `json:"action,omitempty"`
    // VAD + 生理指标（仅当 EmotionalState 可用时填充）
    Mood          string  `json:"mood,omitempty"`
    Valence       float64 `json:"valence,omitempty"`
    Arousal       float64 `json:"arousal,omitempty"`
    Energy        float64 `json:"energy,omitempty"`
    SocialBattery float64 `json:"social_battery,omitempty"`
    Affection     float64 `json:"affection,omitempty"`
    IsJealous     bool    `json:"is_jealous,omitempty"`
    Description   string  `json:"description,omitempty"`
}

// AdminPersonaUpdatePayload 处理前端人设热刷新请求。
type AdminPersonaUpdatePayload struct {
    PersonaID       string `json:"persona_id"`
    Name            string `json:"name,omitempty"`
    Appearance      string `json:"appearance,omitempty"`
    BasePrompt      string `json:"base_prompt,omitempty"`
    SleepyPrompt    string `json:"sleepy_prompt,omitempty"`
    KnowledgeScope  string `json:"knowledge_scope,omitempty"`
    ForbiddenTopics string `json:"forbidden_topics,omitempty"`
}
```

#### [MODIFY] `core/internal/schema/payloads.go`

```go
// PersonaUpdatePayload is the NATS payload for agent.persona.update.
type PersonaUpdatePayload struct {
    PersonaID       string `json:"persona_id"`
    Name            string `json:"name,omitempty"`
    BasePrompt      string `json:"base_prompt,omitempty"`
    SleepyPrompt    string `json:"sleepy_prompt,omitempty"`
    KnowledgeScope  string `json:"knowledge_scope,omitempty"`
    ForbiddenTopics string `json:"forbidden_topics,omitempty"`
}
```

#### [MODIFY] `core/internal/webgateway/nats_bridge.go`

1. **情绪广播增强**：在已有的 `agent.emotion` 广播点（L531, L622），将 `AgentEmotionPayload` 填入完整的 `emotionalState` 数值快照（通过 `b.emotionalState` 读锁安全读取）。

2. **新增 inbound 处理分支**：在 `handleTextFrame` 中处理 `admin.persona_update` 消息类型：

```go
case "admin.persona_update":
    var p AdminPersonaUpdatePayload
    if err := json.Unmarshal(msg.Payload, &p); err != nil {
        return
    }
    natsPayload, _ := json.Marshal(schema.PersonaUpdatePayload{
        PersonaID:       p.PersonaID,
        BasePrompt:      p.BasePrompt,
        SleepyPrompt:    p.SleepyPrompt,
        KnowledgeScope:  p.KnowledgeScope,
        ForbiddenTopics: p.ForbiddenTopics,
    })
    _ = b.bus.Publish("agent.persona.update", natsPayload)
```

---

### Layer 6: Python PersonaLoader NATS 热更新

#### [MODIFY] `shared/persona_loader.py`

新增 NATS `agent.persona.update` 订阅句柄：

```python
async def handle_persona_update(self, raw: bytes) -> None:
    """NATS handler: hot-patch a persona YAML and invalidate in-memory cache."""
    try:
        payload = json.loads(raw)
        persona_id = payload.pop("persona_id", None)
        if not persona_id:
            return
        # 白名单过滤，防止注入非预期字段
        allowed = {"name", "appearance", "base_prompt", "sleepy_prompt",
                   "knowledge_scope", "forbidden_topics"}
        patch = {k: v for k, v in payload.items() if k in allowed and isinstance(v, str) and v}
        if not patch:
            return
        self._patch_yaml(persona_id, patch)
        self.invalidate_cache()
        logger.info("[PersonaLoader] Hot-reloaded persona '%s', fields: %s",
                    persona_id, list(patch.keys()))
    except Exception:
        logger.exception("[PersonaLoader] Failed to handle persona update")

def _patch_yaml(self, persona_id: str, patch: dict) -> None:
    """使用 ruamel.yaml 原地更新 YAML，保留注释和字段顺序。"""
    from ruamel.yaml import YAML
    yaml = YAML()
    yaml.preserve_quotes = True
    path = self._persona_path(persona_id)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.load(f)
    data.update(patch)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)
```

在 `cognitive_engine.py` 中订阅：

```python
await self.nats_client.subscribe("agent.persona.update", cb=self.persona_loader.handle_persona_update)
```

---

## 四、Sprint 执行计划

| Sprint | 预估工时 | 交付内容 | 依赖 |
|:---|:---:|:---|:---|
| **A-1** | ~1天 | `persona-api.ts` + `persona.ts` store + `PromptEditorTab.vue` | 无 |
| **A-2** | ~1天 | `BasicProfileTab.vue` + `BoundaryTab.vue` + Settings 入口卡片 + Tab 路由 | A-1 |
| **A-3** | ~0.5天 | `EmotionWeightTab.vue` + `buildBasePrompt()` 编译器 | A-2 |
| **A-3.5** | ~0.5天 | `EmotionHUDWidget.vue` + `onEmotionState` 监听器 + HUD 开关按钮 | A-1 |
| **A-4** | ~0.5天 | Go WebGateway：`AgentEmotionPayload` 扩充 + `admin.persona_update` inbound handler + `PersonaUpdatePayload` schema | A-1 |
| **A-5** | ~0.5天 | Python `PersonaLoader` NATS 订阅 + `_patch_yaml()` + `ruamel.yaml` 热更新 | A-4 |

> A-1 ~ A-3.5 **纯前端工作**，Go/Python 服务不需要修改，可独立并行推进。  
> A-4 接通后，A-3.5（情绪 HUD）即可获取完整数值结构，之前仅显示 MoodTag 字符串。  
> A-5 接通后，保存按钮的热刷新效果达到生产级别（下一次 LLM 推理立即生效，无需重启 `runner.py`）。

---

## 五、验证计划

### 自动化
- 前端：为 `persona.ts` store 的 `buildBasePrompt()` 编译函数撰写 Vitest 单元测试，覆盖无滑块/部分滑块/全字段场景。
- 后端：新增 `test_sprint8_persona_hotreload.py`，验证 NATS `agent.persona.update` 订阅、`_patch_yaml()` 原地更新，以及 `invalidate_cache()` 后的 `load_persona()` 返回值变化。

### 手动验证流程
1. 打开 AIRI 设置 → 人设与边界，修改 `base_prompt` 任意内容，点击保存。
2. 发送一条消息，确认猫娘回复的措辞/人设符合修改后的 Prompt（**热刷新，无重启**）。
3. 打开 `config/persona/catgirl.yaml`，确认文件已被原地更新且注释保留。
4. 验证 Admin 后台（:8094）未启动时，保存操作降级到本地 AiriCard 存储，并展示 Toast 提示。
5. 点击情绪 HUD 开关，对话几轮后观察好感度 / 精力 / 社交电量数值实时变化。
6. 验证 `IsJealous = true` 时 HUD badge 正确变为 JEALOUS 且红色闪烁。
