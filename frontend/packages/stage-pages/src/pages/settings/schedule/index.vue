<script setup lang="ts">
import { Button } from '@proj-airi/ui'
import { useBetterAgentGatewayStore } from '@proj-airi/stage-ui/stores/modules/betteragent-gateway'
import {
  createSchedule,
  deleteSchedule,
  listSchedules,
  resolveBetterAgentWebId,
  type ScheduleRecord,
} from '@proj-airi/stage-ui/services/betteragent-admin-api'
import { computed, onMounted, ref } from 'vue'

const gatewayStore = useBetterAgentGatewayStore()
const chatId = computed(() => gatewayStore.getResolvedChatId())
const userId = computed(() => resolveBetterAgentWebId(chatId.value))

const schedules = ref<ScheduleRecord[]>([])
const loading = ref(false)
const errorMessage = ref('')
const title = ref('')
const note = ref('')
const remindAt = ref('')
const saving = ref(false)

async function refreshSchedules() {
  if (!chatId.value)
    return
  loading.value = true
  errorMessage.value = ''
  try {
    schedules.value = await listSchedules(chatId.value)
  }
  catch {
    schedules.value = []
    errorMessage.value = '加载日程失败'
  }
  finally {
    loading.value = false
  }
}

async function handleAdd() {
  if (!chatId.value || !title.value.trim() || !remindAt.value)
    return
  saving.value = true
  errorMessage.value = ''
  try {
    const ok = await createSchedule({
      chat_id: chatId.value,
      user_id: userId.value,
      title: title.value.trim(),
      remind_at: remindAt.value,
      note: note.value.trim(),
    })
    if (ok) {
      title.value = ''
      note.value = ''
      remindAt.value = ''
      await refreshSchedules()
    }
    else {
      errorMessage.value = '添加日程失败'
    }
  }
  finally {
    saving.value = false
  }
}

async function handleDelete(scheduleId: string) {
  errorMessage.value = ''
  try {
    await deleteSchedule(scheduleId)
    await refreshSchedules()
  }
  catch {
    errorMessage.value = '删除日程失败'
  }
}

function formatRemindAt(value: string): string {
  if (!value)
    return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime()))
    return value
  return date.toLocaleString()
}

onMounted(refreshSchedules)
</script>

<template>
  <div class="flex flex-col gap-4 pb-4">
    <div v-if="!chatId" class="rounded-xl border border-neutral-200/60 bg-neutral-50/70 p-4 text-sm text-neutral-600 dark:border-neutral-800/60 dark:bg-neutral-900/60 dark:text-neutral-400">
      未获取到当前会话 ID，请先在首页进入聊天。
    </div>

    <div class="rounded-xl border border-neutral-200/60 bg-white/70 p-4 shadow-sm dark:border-neutral-800/60 dark:bg-neutral-900/60">
      <div class="text-lg font-medium">新建日程</div>
      <div class="mt-3 grid grid-cols-1 gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
        <div class="flex flex-col gap-2">
          <input v-model="title" type="text" placeholder="日程标题" class="w-full rounded-lg border border-neutral-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-neutral-800">
          <input v-model="note" type="text" placeholder="备注（可选）" class="w-full rounded-lg border border-neutral-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-neutral-800">
        </div>
        <div class="flex flex-col gap-2">
          <input v-model="remindAt" type="datetime-local" class="rounded-lg border border-neutral-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-neutral-800">
          <Button :disabled="saving || !title.trim() || !remindAt" @click="handleAdd">
            {{ saving ? '添加中…' : '添加日程' }}
          </Button>
        </div>
      </div>
    </div>

    <div class="rounded-xl border border-neutral-200/60 bg-white/70 p-4 shadow-sm dark:border-neutral-800/60 dark:bg-neutral-900/60">
      <div class="text-lg font-medium">已有日程</div>
      <p v-if="errorMessage" class="mt-2 text-sm text-red-500">{{ errorMessage }}</p>
      <div v-if="loading" class="py-6 text-center text-sm text-neutral-500">加载中…</div>
      <div v-else-if="schedules.length === 0" class="py-6 text-center text-sm text-neutral-500">暂无日程</div>
      <ul v-else class="mt-3 flex flex-col gap-2">
        <li v-for="schedule in schedules" :key="schedule.schedule_id" class="flex items-center justify-between gap-3 rounded-lg border border-neutral-200/60 px-3 py-2 dark:border-neutral-800/60">
          <div class="min-w-0">
            <div class="truncate text-sm font-medium">{{ schedule.title }}</div>
            <div class="mt-0.5 truncate text-xs text-neutral-500">{{ formatRemindAt(schedule.remind_at) }}<span v-if="schedule.note"> · {{ schedule.note }}</span></div>
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <span class="rounded-full px-2 py-0.5 text-xs" :class="schedule.status === 'fired' ? 'bg-neutral-200 text-neutral-500' : 'bg-emerald-100 text-emerald-600'">
              {{ schedule.status === 'fired' ? '已触发' : '已计划' }}
            </span>
            <button class="h-8 w-8 rounded-md text-lg text-neutral-500 transition-colors hover:bg-red-100/60 hover:text-red-500" title="删除" @click="handleDelete(schedule.schedule_id)">
              <div class="i-solar:trash-bin-trash-bold-duotone h-5 w-5" />
            </button>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>

<route lang="yaml">
meta:
  layout: settings
  title: 日程提醒
  subtitle: 设置
  description: 管理提醒日程
  icon: i-solar:calendar-mark-bold-duotone
  settingsEntry: true
  order: 6
  stageTransition:
    name: slide
</route>
