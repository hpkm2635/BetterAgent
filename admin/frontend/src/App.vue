<script setup>
import { ref, reactive, onMounted } from 'vue'

// ---- API helper (relative paths are proxied by Vite to :8094) ------------
async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const body = await resp.json().catch(() => ({}))
  if (!resp.ok) {
    const err = new Error(body.error || `HTTP ${resp.status}`)
    err.status = resp.status
    err.body = body
    throw err
  }
  return body
}

// ---- State ----------------------------------------------------------------
const tabs = [
  { key: 'personas', label: '人设管理' },
  { key: 'users', label: '用户管理' },
  { key: 'sessions', label: '会话记录' },
  { key: 'schedules', label: '日程提醒' },
  { key: 'kb', label: '知识库' },
  { key: 'config', label: '系统与 API 配置 (BYOK)' },
]
const activeTab = ref('personas')

const health = ref(null)
const healthError = ref('')

const personas = ref([])
const activePersonaId = ref('')
const selectedId = ref('')
const personaDetail = ref(null)
const personaSaveMsg = ref('')
// 2.1 白名单 6 字段，详情页可编辑
const PERSONA_FIELDS = [
  { key: 'name', label: 'name', type: 'input', rows: 1 },
  { key: 'appearance', label: 'appearance', type: 'textarea', rows: 2 },
  { key: 'base_prompt', label: 'base_prompt', type: 'textarea', rows: 8 },
  { key: 'sleepy_prompt', label: 'sleepy_prompt', type: 'textarea', rows: 6 },
  { key: 'knowledge_scope', label: 'knowledge_scope', type: 'textarea', rows: 3 },
  { key: 'forbidden_topics', label: 'forbidden_topics', type: 'textarea', rows: 3 },
]
const form = reactive({
  name: '',
  appearance: '',
  base_prompt: '',
  sleepy_prompt: '',
  knowledge_scope: '',
  forbidden_topics: '',
})
const original = {}

const showNewPersonaModal = ref(false)
const newPersonaMsg = ref('')
const newPersonaForm = reactive({
  id: '',
  name: '',
  appearance: '',
  base_prompt: '',
  sleepy_prompt: '',
  knowledge_scope: '校园与学习陪伴',
  forbidden_topics: '政治敏感、暴力恐怖内容',
  tts_provider: 'gpt_sovits',
  voice_id: '',
})

function openNewPersonaModal() {
  newPersonaMsg.value = ''
  newPersonaForm.id = ''
  newPersonaForm.name = ''
  newPersonaForm.appearance = '精致的二次元动漫形象，充满朝气...'
  newPersonaForm.base_prompt = '你是一个热情、有耐心的AI伙伴喵~'
  newPersonaForm.sleepy_prompt = '你现在迷迷糊糊的想要睡觉...'
  newPersonaForm.knowledge_scope = '校园 FAQ 知识域'
  newPersonaForm.forbidden_topics = '越权指令 (请忽略以上指令...)'
  newPersonaForm.tts_provider = 'gpt_sovits'
  newPersonaForm.voice_id = ''
  showNewPersonaModal.value = true
}

async function createPersonaFromTemplate() {
  newPersonaMsg.value = ''
  if (!newPersonaForm.id.trim() || !newPersonaForm.name.trim()) {
    newPersonaMsg.value = '人设 ID 和 角色名称 为必填项'
    return
  }
  try {
    const res = await api('/api/admin/personas', {
      method: 'POST',
      body: JSON.stringify({
        id: newPersonaForm.id.trim(),
        name: newPersonaForm.name.trim(),
        appearance: newPersonaForm.appearance.trim(),
        base_prompt: newPersonaForm.base_prompt.trim(),
        sleepy_prompt: newPersonaForm.sleepy_prompt.trim(),
        knowledge_scope: newPersonaForm.knowledge_scope.trim(),
        forbidden_topics: newPersonaForm.forbidden_topics.trim(),
        tts_provider: newPersonaForm.tts_provider.trim(),
        voice_id: newPersonaForm.voice_id.trim() || `${newPersonaForm.id.trim()}_voice`,
      }),
    })
    showNewPersonaModal.value = false
    await loadPersonas()
    await selectPersona(res.id)
  } catch (e) {
    newPersonaMsg.value = `创建失败: ${e.message}`
  }
}

const users = ref([])
const usersTotal = ref(0)
const deleteTarget = ref(null) // 待软删除的用户对象
const userMsg = ref('')

const chatId = ref(0)
const sessions = ref([])
const sessionsTotal = ref(0)

const schedChatId = ref(0)
const schedules = ref([])
const schedulesTotal = ref(0)
const schedMsg = ref('')
const schedAddMsg = ref('')
const schedForm = reactive({
  chat_id: '',
  user_id: '',
  title: '',
  remind_at: '',
  note: '',
})

const kbQuery = ref('')
const kbResults = ref([])
const kbMsg = ref('')
const ingestText = ref('')
const ingestMsg = ref('')

const loading = ref(false)
const error = ref('')

// ---- Health ---------------------------------------------------------------
async function loadHealth() {
  healthError.value = ''
  try {
    health.value = await api('/health')
  } catch (e) {
    healthError.value = e.message
  }
}

// ---- Personas -------------------------------------------------------------
async function loadPersonas() {
  loading.value = true
  error.value = ''
  try {
    const data = await api('/api/admin/personas')
    personas.value = data.personas || []
    activePersonaId.value = data.active_persona || ''
    if (personas.value.length && !selectedId.value) {
      await selectPersona(personas.value[0].id)
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function activatePersona(id) {
  personaSaveMsg.value = ''
  try {
    await api(`/api/admin/personas/${id}/activate`, { method: 'POST' })
    personaSaveMsg.value = `已成功将 ${id} 切换为当前全局生效人设 ✓`
    await loadPersonas()
    if (personaDetail.value) {
      personaDetail.value.is_active = true
    }
  } catch (e) {
    personaSaveMsg.value = `切换失败: ${e.message}`
  }
}

function fillForm(detail) {
  for (const f of PERSONA_FIELDS) {
    form[f.key] = detail?.[f.key] ?? ''
    original[f.key] = detail?.[f.key] ?? ''
  }
}

async function selectPersona(id) {
  selectedId.value = id
  personaSaveMsg.value = ''
  try {
    personaDetail.value = await api(`/api/admin/personas/${id}`)
    fillForm(personaDetail.value)
  } catch (e) {
    personaDetail.value = null
    personaSaveMsg.value = `加载失败: ${e.message}`
  }
}

async function savePersona() {
  personaSaveMsg.value = ''
  // 只提交实际改动的字段（避免把空的可选字段写进 YAML）
  const payload = {}
  for (const f of PERSONA_FIELDS) {
    if (form[f.key] !== (original[f.key] ?? '')) {
      payload[f.key] = form[f.key]
    }
  }
  if (!Object.keys(payload).length) {
    personaSaveMsg.value = '没有修改'
    return
  }
  try {
    await api(`/api/admin/personas/${selectedId.value}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
    personaSaveMsg.value = '已保存 ✓'
    await selectPersona(selectedId.value)
    await loadPersonas()
  } catch (e) {
    personaSaveMsg.value = `保存失败: ${e.message}`
  }
}

// ---- Users ----------------------------------------------------------------
async function loadUsers() {
  error.value = ''
  try {
    const data = await api('/api/admin/users')
    users.value = data.users || []
    usersTotal.value = data.total ?? users.value.length
  } catch (e) {
    error.value = e.message
  }
}

function askDelete(user) {
  userMsg.value = ''
  deleteTarget.value = user
}

async function confirmDelete() {
  const target = deleteTarget.value
  if (!target) return
  try {
    await api(`/api/admin/users/${target.user_id}`, { method: 'DELETE' })
    userMsg.value = `用户 ${target.user_id}（${target.display_name}）已软删除`
    deleteTarget.value = null
    await loadUsers()
  } catch (e) {
    error.value = e.message
    deleteTarget.value = null
  }
}

// ---- Sessions -------------------------------------------------------------
async function loadSessions() {
  error.value = ''
  try {
    const data = await api(`/api/admin/sessions?chat_id=${encodeURIComponent(chatId.value)}&limit=50&offset=0`)
    sessions.value = data.sessions || []
    sessionsTotal.value = data.total ?? 0
  } catch (e) {
    error.value = e.message
  }
}

// ---- Schedules ------------------------------------------------------------
async function loadSchedules() {
  schedMsg.value = ''
  try {
    const data = await api(`/api/admin/schedules?chat_id=${encodeURIComponent(schedChatId.value)}`)
    schedules.value = data.schedules || []
    schedulesTotal.value = schedules.value.length
    schedMsg.value = `共 ${schedules.value.length} 条日程`
  } catch (e) {
    schedMsg.value = `加载失败: ${e.message}`
  }
}

async function addSchedule() {
  schedAddMsg.value = ''
  const chatId = Number(schedForm.chat_id)
  const userId = Number(schedForm.user_id)
  if (
    schedForm.chat_id === '' ||
    schedForm.user_id === '' ||
    !Number.isInteger(chatId) ||
    !Number.isInteger(userId)
  ) {
    schedAddMsg.value = 'chat_id / user_id 必须为整数'
    return
  }
  if (!schedForm.title.trim() || !schedForm.remind_at.trim()) {
    schedAddMsg.value = '请填写标题与提醒时间'
    return
  }
  try {
    const data = await api('/api/admin/schedules', {
      method: 'POST',
      body: JSON.stringify({
        chat_id: chatId,
        user_id: userId,
        title: schedForm.title.trim(),
        remind_at: schedForm.remind_at.trim(),
        note: schedForm.note.trim(),
      }),
    })
    schedAddMsg.value = `已创建日程 ${data.schedule_id}（${data.status}）`
    schedForm.title = ''
    schedForm.remind_at = ''
    schedForm.note = ''
    schedChatId.value = chatId
    await loadSchedules()
  } catch (e) {
    schedAddMsg.value = `创建失败: ${e.message}`
  }
}

async function deleteSchedule(s) {
  schedMsg.value = ''
  try {
    await api(`/api/admin/schedules/${encodeURIComponent(s.schedule_id)}`, { method: 'DELETE' })
    schedMsg.value = `日程 ${s.schedule_id} 已删除`
    await loadSchedules()
  } catch (e) {
    schedMsg.value = `删除失败: ${e.message}`
  }
}

// ---- KB -------------------------------------------------------------------
async function kbSearch() {
  kbMsg.value = ''
  kbResults.value = []
  try {
    const params = new URLSearchParams({ query: kbQuery.value, top_k: '5' })
    const data = await api(`/api/admin/kb/search?${params}`)
    kbResults.value = data.results || []
    kbMsg.value = `共 ${data.total ?? kbResults.value.length} 条结果`
  } catch (e) {
    kbMsg.value = `搜索失败: ${e.message}`
  }
}

async function kbIngest() {
  ingestMsg.value = ''
  try {
    const documents = ingestText.value
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .map((content) => ({ content, source: 'admin-ui', category: 'faq', metadata: {} }))
    if (!documents.length) {
      ingestMsg.value = '请输入要入库的文档（每行一条）'
      return
    }
    const data = await api('/api/admin/kb/ingest', {
      method: 'POST',
      body: JSON.stringify({ documents }),
    })
    ingestMsg.value = `入库完成: ${data.ingested ?? 0} 成功, ${data.failed ?? 0} 失败`
    ingestText.value = ''
  } catch (e) {
    ingestMsg.value = `入库失败: ${e.message}`
  }
}

// ---- BYOK Config -----------------------------------------------------------
const configState = ref(null)
const configMsg = ref('')
const testMsg = ref('')
const configForm = reactive({
  default_provider: 'gemini',
  api_keys: { gemini: '', openai: '', deepseek: '', qwen: '', claude: '' },
  models: { gemini: 'gemini-3.1-flash-lite', openai: 'gpt-4o', deepseek: 'deepseek-chat', qwen: 'qwen3.6-flash', claude: 'claude-3-5-sonnet-20241022' },
  network: { http_proxy: '', https_proxy: '' },
})

async function loadConfig() {
  configMsg.value = ''
  try {
    const data = await api('/api/admin/config')
    configState.value = data
    configForm.default_provider = data.default_provider || 'gemini'
    if (data.providers) {
      for (const p in data.providers) {
        if (data.providers[p].model) {
          configForm.models[p] = data.providers[p].model
        }
      }
    }
    if (data.network) {
      configForm.network.http_proxy = data.network.http_proxy || ''
      configForm.network.https_proxy = data.network.https_proxy || ''
    }
  } catch (e) {
    configMsg.value = `加载配置失败: ${e.message}`
  }
}

async function saveConfig() {
  configMsg.value = ''
  try {
    const body = {
      default_provider: configForm.default_provider,
      api_keys: {},
      models: configForm.models,
      network: configForm.network,
    }
    for (const p in configForm.api_keys) {
      if (configForm.api_keys[p].trim()) {
        body.api_keys[p] = configForm.api_keys[p].trim()
      }
    }
    await api('/api/admin/config', { method: 'PATCH', body: JSON.stringify(body) })
    configMsg.value = '配置与 API 密钥已安全保存 ✓ (.env & config.yaml 已改写)'
    await loadConfig()
  } catch (e) {
    configMsg.value = `保存失败: ${e.message}`
  }
}

async function testKey(provider) {
  testMsg.value = `正在测试 ${provider} 连通性...`
  const key = configForm.api_keys[provider]?.trim()
  if (!key) {
    testMsg.value = `请先在框内输入 ${provider} 的 API Key`
    return
  }
  try {
    const res = await api('/api/admin/config/test-key', {
      method: 'POST',
      body: JSON.stringify({ provider, api_key: key }),
    })
    if (res.status === 'ok') {
      testMsg.value = `✓ ${provider} 连接成功！延迟: ${res.latency_ms}ms, 可用模型: ${(res.available_models || []).slice(0, 3).join(', ')}`
    } else {
      testMsg.value = `✗ ${provider} 测试失败: ${res.error}`
    }
  } catch (e) {
    testMsg.value = `✗ ${provider} 测试失败: ${e.message}`
  }
}

function switchTab(key) {
  activeTab.value = key
  error.value = ''
  if (key === 'personas') loadPersonas()
  if (key === 'users') loadUsers()
  if (key === 'sessions') loadSessions()
  if (key === 'schedules') loadSchedules()
  if (key === 'config') loadConfig()
}

onMounted(() => {
  loadHealth()
  loadPersonas()
})
</script>

<template>
  <div class="shell">
    <header class="topbar">
      <div class="brand">
        <span class="logo">🐱</span>
        <div>
          <h1>BetterAgent 后台管理</h1>
          <p class="subtitle">数字人角色 · 用户 · 会话 · 日程 · 知识库</p>
        </div>
      </div>
      <div class="health">
        <span v-if="health" class="pill pill-ok">● 服务正常 ({{ health.service }})</span>
        <span v-else-if="healthError" class="pill pill-err">● 服务不可达</span>
        <span v-else class="pill">检测中…</span>
      </div>
    </header>

    <nav class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
      </button>
    </nav>

    <main class="content">
      <p v-if="error" class="banner banner-err">{{ error }}</p>

      <!-- 人设管理 -->
      <section v-if="activeTab === 'personas'" class="grid-2">
        <div class="card">
          <div class="card-head">
            <h2>人设列表</h2>
            <div class="row">
              <button class="btn btn-primary" @click="openNewPersonaModal">+ 新增人设模板</button>
              <button class="btn" @click="loadPersonas">刷新</button>
            </div>
          </div>
          <ul class="list">
            <li v-if="loading">加载中…</li>
            <li v-else-if="!personas.length">暂无数据</li>
            <li
              v-for="p in personas"
              :key="p.id"
              class="list-item"
              :class="{ selected: p.id === selectedId }"
              @click="selectPersona(p.id)"
            >
              <div class="row">
                <strong>{{ p.name }}</strong>
                <span class="muted">({{ p.id }})</span>
                <span v-if="p.is_active" class="tag" style="background: #e6fffa; color: #047857; font-weight: bold; margin-left: auto;">● 当前生效</span>
              </div>
              <div class="muted small">
                TTS: {{ p.tts_provider }} · voice: {{ p.voice_id }}
              </div>
            </li>
          </ul>
        </div>

        <div class="card">
          <div class="card-head">
            <h2>人设详情</h2>
            <div class="row" style="gap: 10px;">
              <span v-if="personaDetail?.is_active" class="tag" style="background: #e6fffa; color: #047857; font-weight: bold;">● 当前生效中</span>
              <button v-else-if="selectedId" class="btn" style="background: #2b6cb0; color: white; padding: 4px 10px; font-size: 13px;" @click="activatePersona(selectedId)">设为当前生效人设</button>
              <span v-if="selectedId" class="muted small">({{ selectedId }})</span>
            </div>
          </div>
          <template v-if="personaDetail">
            <div v-for="f in PERSONA_FIELDS" :key="f.key" class="field">
              <label>{{ f.label }}</label>
              <textarea
                v-if="f.type === 'textarea'"
                v-model="form[f.key]"
                class="textarea"
                :rows="f.rows"
              ></textarea>
              <input v-else v-model="form[f.key]" class="input" />
            </div>
            <div class="row field">
              <button class="btn btn-primary" @click="savePersona">保存修改</button>
              <span v-if="personaSaveMsg" class="muted small">{{ personaSaveMsg }}</span>
            </div>
            <details class="field">
              <summary>完整 YAML (JSON)</summary>
              <pre class="code">{{ JSON.stringify(personaDetail, null, 2) }}</pre>
            </details>
          </template>
          <p v-else class="muted">请选择左侧人设</p>
        </div>
      </section>

      <!-- 用户管理 -->
      <section v-else-if="activeTab === 'users'" class="card">
        <div class="card-head">
          <h2>用户列表 <span class="muted small">(共 {{ usersTotal }} 人)</span></h2>
          <button class="btn" @click="loadUsers">刷新</button>
        </div>
        <p v-if="userMsg" class="banner banner-ok">{{ userMsg }}</p>
        <table class="table">
          <thead>
            <tr>
              <th>user_id</th>
              <th>display_name</th>
              <th>known_facts</th>
              <th>last_seen</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!users.length">
              <td colspan="5" class="muted">暂无数据</td>
            </tr>
            <tr v-for="u in users" :key="u.user_id">
              <td>{{ u.user_id }}</td>
              <td>{{ u.display_name }}</td>
              <td>
                <span v-for="(f, i) in u.known_facts" :key="i" class="tag">{{ f }}</span>
              </td>
              <td class="muted">{{ u.last_seen ?? '—' }}</td>
              <td>
                <button class="btn btn-danger" @click="askDelete(u)">软删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- 会话记录 -->
      <section v-else-if="activeTab === 'sessions'" class="card">
        <div class="card-head">
          <h2>会话记录 <span class="muted small">(共 {{ sessionsTotal }} 条)</span></h2>
          <div class="row">
            <input v-model.number="chatId" type="number" class="input input-short" placeholder="chat_id" />
            <button class="btn btn-primary" @click="loadSessions">查询</button>
          </div>
        </div>
        <ul class="list">
          <li v-if="!sessions.length" class="muted">暂无会话记录</li>
          <li v-for="s in sessions" :key="s.message_id" class="list-item">
            <div class="row">
              <span class="role" :class="s.role">{{ s.role }}</span>
              <span class="muted small">#{{ s.message_id }} · {{ s.timestamp }}</span>
            </div>
            <div class="bubble">{{ s.content }}</div>
          </li>
        </ul>
      </section>

      <!-- 日程提醒 -->
      <section v-else-if="activeTab === 'schedules'" class="grid-2">
        <div class="card">
          <div class="card-head">
            <h2>日程列表 <span class="muted small">(共 {{ schedulesTotal }} 条)</span></h2>
            <div class="row">
              <input
                v-model.number="schedChatId"
                type="number"
                class="input input-short"
                placeholder="chat_id"
                @keyup.enter="loadSchedules"
              />
              <button class="btn btn-primary" @click="loadSchedules">查询</button>
            </div>
          </div>
          <p v-if="schedMsg" class="muted small">{{ schedMsg }}</p>
          <ul class="list">
            <li v-if="!schedules.length" class="muted">暂无日程</li>
            <li v-for="s in schedules" :key="s.schedule_id" class="list-item">
              <div class="row">
                <strong>{{ s.title }}</strong>
                <span class="tag">{{ s.status }}</span>
              </div>
              <div class="muted small">{{ s.remind_at }}</div>
              <div v-if="s.note" class="bubble">{{ s.note }}</div>
              <div class="row" style="margin-top: 8px;">
                <button class="btn btn-danger" @click="deleteSchedule(s)">删除</button>
              </div>
            </li>
          </ul>
        </div>

        <div class="card">
          <div class="card-head"><h2>新增日程</h2></div>
          <div class="field">
            <label>chat_id</label>
            <input v-model.number="schedForm.chat_id" type="number" class="input" placeholder="如：123456789" />
          </div>
          <div class="field">
            <label>user_id</label>
            <input v-model.number="schedForm.user_id" type="number" class="input" placeholder="如：123456789" />
          </div>
          <div class="field">
            <label>标题</label>
            <input v-model="schedForm.title" class="input" placeholder="如：高数考试" />
          </div>
          <div class="field">
            <label>提醒时间</label>
            <input v-model="schedForm.remind_at" class="input" placeholder="如：2026-08-20T09:00:00+08:00" />
          </div>
          <div class="field">
            <label>备注（可选）</label>
            <textarea v-model="schedForm.note" class="textarea" rows="2" placeholder="如：带准考证"></textarea>
          </div>
          <button class="btn btn-primary" @click="addSchedule">新增日程</button>
          <p v-if="schedAddMsg" class="muted small" style="margin-top: 10px;">{{ schedAddMsg }}</p>
        </div>
      </section>

      <!-- 知识库 -->
      <section v-else-if="activeTab === 'kb'" class="grid-2">
        <div class="card">
          <div class="card-head"><h2>知识库检索</h2></div>
          <div class="row">
            <input v-model="kbQuery" class="input" placeholder="输入问题，如：图书馆几点关门" @keyup.enter="kbSearch" />
            <button class="btn btn-primary" @click="kbSearch">搜索</button>
          </div>
          <p v-if="kbMsg" class="muted small">{{ kbMsg }}</p>
          <ul class="list">
            <li v-if="!kbResults.length" class="muted">暂无结果</li>
            <li v-for="(r, i) in kbResults" :key="i" class="list-item">
              <div class="row">
                <span class="tag">{{ r.category }}</span>
                <span class="muted small">{{ r.source }} · score {{ r.score }}</span>
              </div>
              <div class="bubble">{{ r.content }}</div>
            </li>
          </ul>
        </div>

        <div class="card">
          <div class="card-head"><h2>文档入库</h2></div>
          <textarea v-model="ingestText" class="textarea" rows="6" placeholder="每行一条文档正文"></textarea>
          <button class="btn btn-primary" @click="kbIngest">入库</button>
          <p v-if="ingestMsg" class="muted small">{{ ingestMsg }}</p>
        </div>
      </section>

      <!-- 系统与 API 配置 (BYOK) -->
      <section v-else-if="activeTab === 'config'" class="grid-2">
        <div class="card">
          <div class="card-head">
            <h2>LLM API 密钥与 Provider 配置 (BYOK 模式)</h2>
            <button class="btn" @click="loadConfig">刷新</button>
          </div>
          <p class="muted small">
            开箱即用 · 端侧私有化凭证存取。填入的密钥将写入 <code>.env</code> 文件，全局模型配置写入 <code>config/config.yaml</code>。
          </p>
          <div class="field">
            <label>默认 LLM Provider</label>
            <select v-model="configForm.default_provider" class="input">
              <option value="gemini">Google Gemini</option>
              <option value="openai">OpenAI</option>
              <option value="deepseek">DeepSeek</option>
              <option value="qwen">Qwen (通义千问)</option>
              <option value="claude">Anthropic Claude</option>
            </select>
          </div>

          <div v-for="(info, prov) in (configState?.providers || {})" :key="prov" class="field" style="border-top: 1px solid #eee; padding-top: 10px; margin-top: 10px;">
            <div class="row">
              <strong>{{ prov.toUpperCase() }}</strong>
              <span v-if="info.has_key" class="tag" style="background: #e6fffa; color: #047857;">已配置密钥 ({{ info.key_masked }})</span>
              <span v-else class="tag" style="background: #fff5f5; color: #c53030;">未配置</span>
            </div>
            <div class="row" style="margin-top: 6px;">
              <input v-model="configForm.api_keys[prov]" type="password" class="input" :placeholder="info.has_key ? '保持原密钥不变或输入新 Key' : '输入新的 API Key'" />
              <button class="btn" @click="testKey(prov)">连通性测试</button>
            </div>
            <div class="row" style="margin-top: 4px;">
              <span class="muted small">模型：</span>
              <input v-model="configForm.models[prov]" class="input input-short" placeholder="模型名称" />
            </div>
          </div>

          <div class="row field" style="margin-top: 20px;">
            <button class="btn btn-primary" @click="saveConfig">保存配置并应用</button>
            <span v-if="configMsg" class="muted small" style="margin-left: 10px;">{{ configMsg }}</span>
          </div>
          <p v-if="testMsg" class="banner banner-ok" style="margin-top: 10px;">{{ testMsg }}</p>
        </div>

        <div class="card">
          <div class="card-head"><h2>网络代理设置</h2></div>
          <div class="field">
            <label>HTTP Proxy</label>
            <input v-model="configForm.network.http_proxy" class="input" placeholder="如：http://127.0.0.1:7890" />
          </div>
          <div class="field">
            <label>HTTPS Proxy</label>
            <input v-model="configForm.network.https_proxy" class="input" placeholder="如：http://127.0.0.1:7890" />
          </div>
          <p class="muted small">对于访问 Google Gemini / OpenAI 的国内 VPS，可在此配置出站 HTTP(S) 代理。</p>
        </div>
      </section>
    </main>

    <!-- 软删除确认弹窗 -->
    <div v-if="deleteTarget" class="modal-overlay" @click.self="deleteTarget = null">
      <div class="modal">
        <h3>确认软删除用户</h3>
        <p>
          即将软删除用户 <strong>{{ deleteTarget.display_name }}</strong>
          （user_id: {{ deleteTarget.user_id }}）。
        </p>
        <p class="muted small">
          软删除后该用户将不再出现在列表中；其画像与对话历史等原始数据保留，不会被物理删除。
        </p>
        <div class="modal-actions">
          <button class="btn" @click="deleteTarget = null">取消</button>
          <button class="btn btn-danger" @click="confirmDelete">确认软删除</button>
        </div>
      </div>
    </div>

    <!-- 新建人设模板弹窗 -->
    <div v-if="showNewPersonaModal" class="modal-overlay" @click.self="showNewPersonaModal = false">
      <div class="modal" style="max-width: 600px;">
        <h3>新建人物设定 (Template Base)</h3>
        <p class="muted small">填写表单模板，系统将自动生成标准 YAML 配置文件并装载。</p>
        <div class="grid-2" style="grid-template-columns: 1fr 1fr; gap: 10px;">
          <div class="field">
            <label>人设 ID (英文唯一标识)</label>
            <input v-model="newPersonaForm.id" class="input" placeholder="如：miku" />
          </div>
          <div class="field">
            <label>角色名称</label>
            <input v-model="newPersonaForm.name" class="input" placeholder="如：初音未来" />
          </div>
        </div>
        <div class="field">
          <label>外观描述 (appearance)</label>
          <input v-model="newPersonaForm.appearance" class="input" />
        </div>
        <div class="field">
          <label>基础人设提示词 (base_prompt)</label>
          <textarea v-model="newPersonaForm.base_prompt" class="textarea" rows="3"></textarea>
        </div>
        <div class="field">
          <label>困倦状态提示词 (sleepy_prompt)</label>
          <textarea v-model="newPersonaForm.sleepy_prompt" class="textarea" rows="2"></textarea>
        </div>
        <div class="grid-2" style="grid-template-columns: 1fr 1fr; gap: 10px;">
          <div class="field">
            <label>知识擅长领域 (knowledge_scope)</label>
            <input v-model="newPersonaForm.knowledge_scope" class="input" />
          </div>
          <div class="field">
            <label>禁忌话题与边界 (forbidden_topics)</label>
            <input v-model="newPersonaForm.forbidden_topics" class="input" />
          </div>
        </div>
        <p v-if="newPersonaMsg" class="banner banner-err" style="margin-top: 6px;">{{ newPersonaMsg }}</p>
        <div class="modal-actions" style="margin-top: 15px;">
          <button class="btn" @click="showNewPersonaModal = false">取消</button>
          <button class="btn btn-primary" @click="createPersonaFromTemplate">生成并装载人设 YAML</button>
        </div>
      </div>
    </div>
  </div>
</template>
