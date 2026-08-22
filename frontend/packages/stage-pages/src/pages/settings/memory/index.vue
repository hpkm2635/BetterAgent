<script setup lang="ts">
import { Button, DoubleCheckButton } from '@proj-airi/ui'
import { useBetterAgentGatewayStore } from '@proj-airi/stage-ui/stores/modules/betteragent-gateway'
import {
  clearShortTermMemory,
  deleteLongTermMemory,
  getLongTermMemory,
  getMemoryProfile,
  getShortTermMemory,
  resolveBetterAgentWebId,
  updateMemoryProfile,
  type LongTermMemory,
  type MemoryProfile,
  type SessionMessage,
} from '@proj-airi/stage-ui/services/betteragent-admin-api'
import { computed, onMounted, ref } from 'vue'

const gatewayStore = useBetterAgentGatewayStore()
const chatId = computed(() => gatewayStore.getResolvedChatId())
const userId = computed(() => resolveBetterAgentWebId(chatId.value))

const profile = ref<MemoryProfile | null>(null)
const displayName = ref('')
const factsText = ref('')
const shortTerm = ref<SessionMessage[]>([])
const longTerm = ref<LongTermMemory[]>([])
const longTermQuery = ref('')
const loading = ref(false)
const message = ref('')
const saveStatus = ref<'idle' | 'success' | 'error'>('idle')

async function refreshProfile() {
  profile.value = await getMemoryProfile(userId.value)
  if (profile.value) {
    displayName.value = profile.value.display_name ?? ''
    factsText.value = (profile.value.known_facts ?? []).join('\n')
  }
}

async function refreshShortTerm() {
  shortTerm.value = await getShortTermMemory(userId.value)
}

async function refreshLongTerm() {
  longTerm.value = await getLongTermMemory(userId.value, longTermQuery.value)
}

async function refreshAll() {
  if (!chatId.value)
    return
  loading.value = true
  try {
    await Promise.all([refreshProfile(), refreshShortTerm(), refreshLongTerm()])
  }
  finally {
    loading.value = false
  }
}

async function saveProfile() {
  message.value = ''
  saveStatus.value = 'idle'
  const facts = factsText.value
    .split('\n')
    .map(item => item.trim())
    .filter(Boolean)
  const updated = await updateMemoryProfile(userId.value, {
    display_name: displayName.value.trim(),
    known_facts: facts,
  })
  if (updated) {
    profile.value = updated
    message.value = '画像已保存'
    saveStatus.value = 'success'
  }
  else {
    message.value = '保存画像失败'
    saveStatus.value = 'error'
  }
}

async function handleClearShortTerm() {
  message.value = ''
  const ok = await clearShortTermMemory(userId.value)
  if (ok) {
    await refreshShortTerm()
    message.value = '短期记忆已清空'
  }
  else {
    message.value = '清空短期记忆失败'
  }
}

async function handleDeleteLongTerm(id: string) {
  message.value = ''
  const ok = await deleteLongTermMemory(id)
  if (ok)
    await refreshLongTerm()
  else
    message.value = '删除长期记忆失败'
}

function formatTimestamp(value?: number): string {
  if (!value)
    return ''
  const date = new Date(value * 1000)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString()
}

onMounted(refreshAll)
</script>

<template>
  <div class="flex flex-col gap-4 pb-4">
    <div v-if="!chatId" class="rounded-xl border border-neutral-200/60 bg-neutral-50/70 p-4 text-sm text-neutral-600 dark:border-neutral-800/60 dark:bg-neutral-900/60 dark:text-neutral-400">
      未获取到当前会话 ID，请先在首页进入聊天。
    </div>

    <div v-if="message" class="rounded-lg px-3 py-2 text-sm" :class="saveStatus === 'success' ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : saveStatus === 'error' ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' : 'bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400'">
      {{ message }}
    </div>

    <div class="rounded-xl border border-neutral-200/60 bg-white/70 p-4 shadow-sm dark:border-neutral-800/60 dark:bg-neutral-900/60">
      <div class="text-lg font-medium">用户画像</div>
      <div class="mt-3 flex flex-col gap-2">
        <input v-model="displayName" type="text" placeholder="称呼" class="w-full rounded-lg border border-neutral-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-neutral-800">
        <textarea v-model="factsText" rows="3" placeholder="每行一条记忆事实" class="w-full resize-none rounded-lg border border-neutral-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-neutral-800" />
        <div class="flex justify-end">
          <Button @click="saveProfile">保存画像</Button>
        </div>
      </div>
    </div>

    <div class="rounded-xl border border-neutral-200/60 bg-white/70 p-4 shadow-sm dark:border-neutral-800/60 dark:bg-neutral-900/60">
      <div class="flex items-center justify-between gap-3">
        <div class="text-lg font-medium">短期记忆</div>
        <DoubleCheckButton @confirm="handleClearShortTerm">
          清空短期记忆
          <template #confirm>确认</template>
          <template #cancel>取消</template>
        </DoubleCheckButton>
      </div>
      <div v-if="loading" class="py-6 text-center text-sm text-neutral-500">加载中…</div>
      <div v-else-if="shortTerm.length === 0" class="py-6 text-center text-sm text-neutral-500">暂无短期记忆</div>
      <ul v-else class="mt-3 flex max-h-80 flex-col gap-2 overflow-y-auto">
        <li v-for="(item, index) in shortTerm" :key="`${item.message_id}-${index}`" class="rounded-lg border border-neutral-200/60 px-3 py-2 dark:border-neutral-800/60">
          <div class="flex items-center gap-2 text-xs text-neutral-500">
            <span class="rounded-full bg-neutral-100 px-2 py-0.5 dark:bg-neutral-800">{{ item.role }}</span>
            <span>{{ formatTimestamp(item.timestamp) }}</span>
          </div>
          <div class="mt-1 whitespace-pre-wrap text-sm">{{ item.content }}</div>
        </li>
      </ul>
    </div>

    <div class="rounded-xl border border-neutral-200/60 bg-white/70 p-4 shadow-sm dark:border-neutral-800/60 dark:bg-neutral-900/60">
      <div class="text-lg font-medium">长期记忆</div>
      <div class="mt-3 flex gap-2">
        <input v-model="longTermQuery" type="text" placeholder="搜索长期记忆" class="w-full rounded-lg border border-neutral-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-neutral-800" @keyup.enter="refreshLongTerm">
        <Button @click="refreshLongTerm">搜索</Button>
      </div>
      <div v-if="longTerm.length === 0" class="py-6 text-center text-sm text-neutral-500">暂无长期记忆</div>
      <ul v-else class="mt-3 flex flex-col gap-2">
        <li v-for="memory in longTerm" :key="memory.id" class="flex items-start justify-between gap-3 rounded-lg border border-neutral-200/60 px-3 py-2 dark:border-neutral-800/60">
          <div class="min-w-0">
            <div class="whitespace-pre-wrap text-sm">{{ memory.text }}</div>
            <div v-if="memory.timestamp" class="mt-0.5 text-xs text-neutral-500">{{ formatTimestamp(memory.timestamp) }}</div>
          </div>
          <button class="h-8 w-8 shrink-0 rounded-md text-lg text-neutral-500 transition-colors hover:bg-red-100/60 hover:text-red-500" title="删除" @click="handleDeleteLongTerm(memory.id)">
            <div class="i-solar:trash-bin-trash-bold-duotone h-5 w-5" />
          </button>
        </li>
      </ul>
    </div>
  </div>
</template>

<route lang="yaml">
meta:
  layout: settings
  titleKey: settings.pages.memory.title
  subtitleKey: settings.title
  descriptionKey: settings.pages.memory.description
  icon: i-solar:leaf-bold-duotone
  settingsEntry: true
  order: 5
  stageTransition:
    name: slide
</route>
