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
  { key: 'config', label: '系统与 API 配置' },
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

const users = ref([])
const usersTotal = ref(0)
const deleteTarget = ref(null) // 待软删除的用户对象
const userMsg = ref('')

const chatId = ref(0)
const sessions = ref([])
const sessionsTotal = ref(0)

const schedChatId = ref(1001)
const schedules = ref([])
const schedulesTotal = ref(0)
const schedMsg = ref('')
const schedAddMsg = ref('')
const schedForm = reactive({
  chat_id: 1001,
  user_id: 1,
  title: '',
  remind_at: '',
  note: '',
})

const kbQuery = ref('')
const kbResults = ref([])
const kbMsg = ref('')
const ingestText = ref('')
const ingestMsg = ref('')

// ---- 系统与 API 配置 (BYOK) ------------------------------------------------
const sysConfig = ref(null)          // GET /api/admin/config 原始响应
const configMsg = ref('')
const configSaving = ref(false)
const defaultProvider = ref('')
const httpProxy = ref('')
const httpsProxy = ref('')
const providers = ref([])            // { name, model, keySet, keyMasked, keyInput, testResult, testing }

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
    activePersonaId.value = data.active_id || ''
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
    const res = await api(`/api/admin/personas/${id}/activate`, { method: 'POST' })
    activePersonaId.value = res.active_id || id
    personaSaveMsg.value = '已设为当前系统活跃人设 ✓'
    await loadPersonas()
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

const deletePersonaTarget = ref(null)

function askDeletePersona(persona) {
  personaSaveMsg.value = ''
  deletePersonaTarget.value = persona
}

async function confirmDeletePersona() {
  const target = deletePersonaTarget.value
  if (!target) return
  try {
    await api(`/api/admin/personas/${target.id}`, { method: 'DELETE' })
    deletePersonaTarget.value = null
    selectedId.value = ''
    personaDetail.value = null
    await loadPersonas()
  } catch (e) {
    personaSaveMsg.value = `删除失败: ${e.message}`
    deletePersonaTarget.value = null
  }
}

const showCreateModal = ref(false)
const createMsg = ref('')
const newPersona = reactive({
  id: '',
  name: '',
  appearance: '',
  base_prompt: '',
  knowledge_scope: '日常陪伴',
  forbidden_topics: '敏感及违规话题',
})

async function handleCreatePersona() {
  createMsg.value = ''
  if (!newPersona.id.trim() || !newPersona.name.trim()) {
    createMsg.value = '请填写 ID 与 角色名称'
    return
  }
  try {
    await api('/api/admin/personas', {
      method: 'POST',
      body: JSON.stringify(newPersona),
    })
    createMsg.value = '创建成功 ✓'
    showCreateModal.value = false
    const createdId = newPersona.id.trim()
    newPersona.id = ''
    newPersona.name = ''
    newPersona.appearance = ''
    newPersona.base_prompt = ''
    await loadPersonas()
    await selectPersona(createdId)
  } catch (e) {
    createMsg.value = `创建失败: ${e.message}`
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
    if (data.chat_id) {
      chatId.value = data.chat_id
    }
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
  let formattedTime = schedForm.remind_at.trim().replace('T', ' ')
  if (formattedTime.length === 16) {
    formattedTime += ':00'
  }

  try {
    const data = await api('/api/admin/schedules', {
      method: 'POST',
      body: JSON.stringify({
        chat_id: chatId,
        user_id: userId,
        title: schedForm.title.trim(),
        remind_at: formattedTime,
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

// ---- 系统与 API 配置 -------------------------------------------------------
async function loadConfig() {
  configMsg.value = ''
  try {
    const data = await api('/api/admin/config')
    sysConfig.value = data
    defaultProvider.value = data.default_provider || 'gemini'
    httpProxy.value = data.network?.http_proxy || ''
    httpsProxy.value = data.network?.https_proxy || ''
    providers.value = (data.providers || []).map((p) => ({
      name: p.name,
      model: p.model || '',
      keySet: !!p.key_set,
      keyMasked: p.key_masked || null,
      keyInput: '',
      testResult: null,
      testing: false,
    }))
  } catch (e) {
    configMsg.value = `加载失败: ${e.message}`
  }
}

async function saveConfig() {
  configMsg.value = ''
  configSaving.value = true
  try {
    const payload = {}
    if (defaultProvider.value !== (sysConfig.value?.default_provider || 'gemini')) {
      payload.default_provider = defaultProvider.value
    }
    const network = {}
    if (httpProxy.value !== (sysConfig.value?.network?.http_proxy || '')) network.http_proxy = httpProxy.value
    if (httpsProxy.value !== (sysConfig.value?.network?.https_proxy || '')) network.https_proxy = httpsProxy.value
    if (Object.keys(network).length) payload.network = network

    const provUpdates = {}
    for (const p of providers.value) {
      const orig = (sysConfig.value?.providers || []).find((x) => x.name === p.name) || {}
      const update = {}
      if (p.keyInput && p.keyInput.trim()) update.api_key = p.keyInput.trim()
      if (p.model.trim() && p.model.trim() !== (orig.model || '')) update.model = p.model.trim()
      if (Object.keys(update).length) provUpdates[p.name] = update
    }
    if (Object.keys(provUpdates).length) payload.providers = provUpdates

    if (!Object.keys(payload).length) {
      configMsg.value = '没有改动'
      return
    }
    const res = await api('/api/admin/config', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
    configMsg.value = res.reloaded ? '已保存并触发全服务热刷新 ✓' : '已保存 ✓'
    await loadConfig()
  } catch (e) {
    configMsg.value = `保存失败: ${e.message}`
  } finally {
    configSaving.value = false
  }
}

async function testKey(name) {
  const p = providers.value.find((x) => x.name === name)
  if (!p) return
  p.testing = true
  p.testResult = null
  try {
    const body = { provider: name }
    if (p.keyInput && p.keyInput.trim()) body.api_key = p.keyInput.trim()
    const res = await api('/api/admin/config/test-key', {
      method: 'POST',
      body: JSON.stringify(body),
    })
    p.testResult = res
  } catch (e) {
    p.testResult = { ok: false, error: e.message }
  } finally {
    p.testing = false
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
          <p class="subtitle">数字人角色 · 用户 · 会话 · 日程 · 知识库 · 系统与 API 配置</p>
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
              <button class="btn btn-primary" @click="showCreateModal = true">+ 新增人设</button>
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
                <span v-if="p.is_active || p.id === activePersonaId" class="tag tag-active">当前活跃</span>
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
            <div v-if="selectedId" class="row">
              <span v-if="selectedId === activePersonaId" class="tag tag-active">当前活跃人设</span>
              <span class="muted small">{{ selectedId }}</span>
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
              <button
                v-if="selectedId && selectedId !== activePersonaId"
                class="btn btn-success"
                @click="activatePersona(selectedId)"
              >
                设为当前活跃人设
              </button>
              <button v-if="selectedId !== 'catgirl' && selectedId !== activePersonaId" class="btn btn-danger" @click="askDeletePersona(personaDetail)">删除人设</button>
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
            <input v-model="schedForm.remind_at" type="datetime-local" class="input" />
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

      <!-- 系统与 API 配置 -->
      <section v-else-if="activeTab === 'config'" class="grid-2">
        <div class="card">
          <div class="card-head">
            <h2>默认 Provider 与网络代理</h2>
            <button class="btn" @click="loadConfig">刷新</button>
          </div>
          <p v-if="configMsg" class="banner" :class="configMsg.startsWith('加载失败') || configMsg.startsWith('保存失败') ? 'banner-err' : 'banner-ok'">{{ configMsg }}</p>
          <div class="field">
            <label>默认 LLM Provider</label>
            <select v-model="defaultProvider" class="input select">
              <option v-for="p in providers" :key="p.name" :value="p.name">{{ p.name }}</option>
            </select>
          </div>
          <div class="field">
            <label>HTTP 代理</label>
            <input v-model="httpProxy" class="input" placeholder="如 http://127.0.0.1:7890（留空不使用）" />
          </div>
          <div class="field">
            <label>HTTPS 代理</label>
            <input v-model="httpsProxy" class="input" placeholder="如 http://127.0.0.1:7890（留空不使用）" />
          </div>
          <button class="btn btn-primary" :disabled="configSaving" @click="saveConfig">
            {{ configSaving ? '保存中…' : '保存配置' }}
          </button>
        </div>

        <div class="card">
          <div class="card-head"><h2>Provider API Key</h2></div>
          <p class="muted small" style="margin-top: -6px; margin-bottom: 12px;">
            密钥仅保存在本机 <code>.env</code>，界面以脱敏形式展示；「测试」不会保存任何配置。
          </p>
          <div v-for="p in providers" :key="p.name" class="provider-box">
            <div class="row">
              <strong>{{ p.name }}</strong>
              <span v-if="p.keySet" class="pill pill-ok">{{ p.keyMasked }}</span>
              <span v-else class="pill pill-err">未设置</span>
            </div>
            <div class="row" style="margin-top: 8px;">
              <input v-model="p.keyInput" type="password" class="input" placeholder="输入新 API Key（留空表示不修改）" />
              <button class="btn" :disabled="p.testing" @click="testKey(p.name)">{{ p.testing ? '测试中…' : '测试' }}</button>
            </div>
            <div class="row" style="margin-top: 8px;">
              <input v-model="p.model" class="input" placeholder="模型名（如 gemini-2.5-flash）" />
            </div>
            <div v-if="p.testResult" class="small" style="margin-top: 8px;">
              <span v-if="p.testResult.ok" class="pill pill-ok">
                ✓ {{ p.testResult.latency_ms }}ms · {{ (p.testResult.models || []).join(', ') || '可用' }}
              </span>
              <span v-else class="pill pill-err">✗ {{ p.testResult.error }}</span>
            </div>
          </div>
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

    <!-- 新增人设弹窗 -->
    <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
      <div class="modal" style="max-width: 520px;">
        <h3>新增人设模板</h3>
        <div class="field">
          <label>人设 ID（英文字母/数字/下划线）</label>
          <input v-model="newPersona.id" class="input" placeholder="如：shizuku" />
        </div>
        <div class="field">
          <label>角色名称</label>
          <input v-model="newPersona.name" class="input" placeholder="如：Shizuku" />
        </div>
        <div class="field">
          <label>外貌设定 (appearance)</label>
          <textarea v-model="newPersona.appearance" class="textarea" rows="2" placeholder="如：黑发双马尾少女，身穿制服..."></textarea>
        </div>
        <div class="field">
          <label>基础 Prompt (base_prompt)</label>
          <textarea v-model="newPersona.base_prompt" class="textarea" rows="4" placeholder="如：你是一个性格温和、贴心的伴侣..."></textarea>
        </div>
        <div class="field">
          <label>知识专业范围 (knowledge_scope)</label>
          <input v-model="newPersona.knowledge_scope" class="input" placeholder="如：日常陪伴、情感倾听" />
        </div>
        <div class="field">
          <label>禁忌话题 (forbidden_topics)</label>
          <input v-model="newPersona.forbidden_topics" class="input" placeholder="如：违法违规话题" />
        </div>
        <p v-if="createMsg" class="muted small" style="margin-top: 8px;">{{ createMsg }}</p>
        <div class="modal-actions" style="margin-top: 16px;">
          <button class="btn" @click="showCreateModal = false">取消</button>
          <button class="btn btn-primary" @click="handleCreatePersona">创建人设</button>
        </div>
      </div>
    </div>
    <!-- 删除人设确认弹窗 -->
    <div v-if="deletePersonaTarget" class="modal-overlay" @click.self="deletePersonaTarget = null">
      <div class="modal">
        <h3>确认删除人设</h3>
        <p>
          即将永久删除人设配置文件 <strong>{{ deletePersonaTarget.name }}</strong>
          （id: {{ deletePersonaTarget.id }}.yaml）。
        </p>
        <p class="muted small">
          删除后该 YAML 配置文件将被物理移除，请确认是否删除。
        </p>
        <div class="modal-actions">
          <button class="btn" @click="deletePersonaTarget = null">取消</button>
          <button class="btn btn-danger" @click="confirmDeletePersona">确认删除</button>
        </div>
      </div>
    </div>
  </div>
</template>
