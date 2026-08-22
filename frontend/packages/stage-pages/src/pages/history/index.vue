<script setup lang="ts">
import { Button } from '@proj-airi/ui'
import {
  getSessionMessages,
  getSessionOverview,
  type SessionMessage,
  type SessionSummary,
} from '@proj-airi/stage-ui/services/betteragent-admin-api'
import { onMounted, ref } from 'vue'

const sessions = ref<SessionSummary[]>([])
const selected = ref<SessionSummary | null>(null)
const messages = ref<SessionMessage[]>([])
const loading = ref(false)
const messagesLoading = ref(false)
const errorMessage = ref('')

async function loadOverview() {
  loading.value = true
  errorMessage.value = ''
  try {
    sessions.value = await getSessionOverview()
  }
  catch {
    sessions.value = []
    errorMessage.value = '加载历史会话失败'
  }
  finally {
    loading.value = false
  }
}

async function openSession(session: SessionSummary) {
  selected.value = session
  messagesLoading.value = true
  errorMessage.value = ''
  try {
    messages.value = await getSessionMessages(session.chat_id)
  }
  catch {
    messages.value = []
    errorMessage.value = '加载会话内容失败'
  }
  finally {
    messagesLoading.value = false
  }
}

function formatTimestamp(value?: number | null): string {
  if (!value)
    return ''
  const date = new Date(value * 1000)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString()
}

onMounted(loadOverview)
</script>

<template>
  <div class="mx-auto flex h-full w-full max-w-5xl flex-col gap-3">
    <div class="flex items-center justify-between">
      <div>
        <div class="text-lg font-medium">历史会话</div>
        <p class="text-sm text-neutral-500">最近 24 小时内的短期对话记录</p>
      </div>
      <Button @click="loadOverview">刷新</Button>
    </div>

    <p v-if="errorMessage" class="text-sm text-red-500">{{ errorMessage }}</p>

    <div class="grid min-h-0 flex-1 grid-cols-1 gap-3 md:grid-cols-[minmax(0,280px)_minmax(0,1fr)]">
      <div class="rounded-xl border border-neutral-200/60 bg-white/70 p-3 shadow-sm dark:border-neutral-800/60 dark:bg-neutral-900/60">
        <div v-if="loading" class="py-6 text-center text-sm text-neutral-500">加载中…</div>
        <div v-else-if="sessions.length === 0" class="py-6 text-center text-sm text-neutral-500">暂无历史会话</div>
        <ul v-else class="flex flex-col gap-2">
          <li v-for="session in sessions" :key="session.chat_id">
            <button
              class="w-full rounded-lg border border-transparent px-3 py-2 text-left transition-colors"
              :class="selected?.chat_id === session.chat_id ? 'border-primary-300 bg-primary-100/60 dark:border-primary-700 dark:bg-primary-900/40' : 'hover:bg-neutral-100/70 dark:hover:bg-neutral-800/60'"
              @click="openSession(session)"
            >
              <div class="flex items-center justify-between gap-2">
                <span class="text-sm font-medium">会话 {{ session.chat_id }}</span>
                <span class="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-500 dark:bg-neutral-800">{{ session.message_count }} 条</span>
              </div>
              <div v-if="session.preview" class="mt-1 truncate text-xs text-neutral-500">{{ session.preview }}</div>
              <div v-if="session.last_timestamp" class="mt-0.5 text-xs text-neutral-400">{{ formatTimestamp(session.last_timestamp) }}</div>
            </button>
          </li>
        </ul>
      </div>

      <div class="rounded-xl border border-neutral-200/60 bg-white/70 p-4 shadow-sm dark:border-neutral-800/60 dark:bg-neutral-900/60">
        <div v-if="!selected" class="py-10 text-center text-sm text-neutral-500">选择左侧会话查看内容</div>
        <div v-else-if="messagesLoading" class="py-10 text-center text-sm text-neutral-500">加载中…</div>
        <div v-else-if="messages.length === 0" class="py-10 text-center text-sm text-neutral-500">该会话暂无消息</div>
        <ul v-else class="flex max-h-[60vh] flex-col gap-2 overflow-y-auto">
          <li v-for="(message, index) in messages" :key="`${message.message_id}-${index}`" class="rounded-lg border border-neutral-200/60 px-3 py-2 dark:border-neutral-800/60">
            <div class="flex items-center gap-2 text-xs text-neutral-500">
              <span class="rounded-full bg-neutral-100 px-2 py-0.5 dark:bg-neutral-800">{{ message.role }}</span>
              <span>{{ formatTimestamp(message.timestamp) }}</span>
            </div>
            <div class="mt-1 whitespace-pre-wrap text-sm">{{ message.content }}</div>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<route lang="yaml">
name: HistoryPage
meta:
  layout: default
  title: 历史会话
</route>
