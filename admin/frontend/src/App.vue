<script setup>
import { ref, onMounted } from 'vue'

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
  { key: 'kb', label: '知识库' },
]
const activeTab = ref('personas')

const health = ref(null)
const healthError = ref('')

const personas = ref([])
const selectedId = ref('')
const personaDetail = ref(null)
const editName = ref('')
const editMsg = ref('')

const users = ref([])
const usersTotal = ref(0)

const chatId = ref(0)
const sessions = ref([])
const sessionsTotal = ref(0)

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
    if (personas.value.length && !selectedId.value) {
      await selectPersona(personas.value[0].id)
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function selectPersona(id) {
  selectedId.value = id
  editMsg.value = ''
  try {
    personaDetail.value = await api(`/api/admin/personas/${id}`)
    editName.value = personaDetail.value.name || ''
  } catch (e) {
    personaDetail.value = null
    editMsg.value = `加载失败: ${e.message}`
  }
}

async function saveName() {
  editMsg.value = ''
  try {
    await api(`/api/admin/personas/${selectedId.value}`, {
      method: 'PATCH',
      body: JSON.stringify({ name: editName.value }),
    })
    editMsg.value = '已保存 ✓'
    await selectPersona(selectedId.value)
    await loadPersonas()
  } catch (e) {
    editMsg.value = `保存失败: ${e.message}`
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

async function deleteUser(userId) {
  if (!window.confirm(`确定软删除用户 ${userId} 吗？`)) return
  try {
    await api(`/api/admin/users/${userId}`, { method: 'DELETE' })
    await loadUsers()
  } catch (e) {
    error.value = e.message
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

function switchTab(key) {
  activeTab.value = key
  error.value = ''
  if (key === 'personas') loadPersonas()
  if (key === 'users') loadUsers()
  if (key === 'sessions') loadSessions()
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
          <p class="subtitle">数字人角色 · 用户 · 会话 · 知识库</p>
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
            <button class="btn" @click="loadPersonas">刷新</button>
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
          </div>
          <template v-if="personaDetail">
            <div class="field">
              <label>name</label>
              <div class="row">
                <input v-model="editName" class="input" />
                <button class="btn btn-primary" @click="saveName">保存</button>
              </div>
              <p v-if="editMsg" class="muted small">{{ editMsg }}</p>
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
        <table class="table">
          <thead>
            <tr>
              <th>user_id</th>
              <th>display_name</th>
              <th>known_facts</th>
              <th>last_seen</th>
              <th></th>
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
                <button class="btn btn-danger" @click="deleteUser(u.user_id)">软删除</button>
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
    </main>
  </div>
</template>
