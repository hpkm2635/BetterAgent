<script setup lang="ts">
import type { ScheduleItem } from '@proj-airi/stage-ui/services/schedule-api'
import { addSchedule, deleteSchedule, fetchSchedules } from '@proj-airi/stage-ui/services/schedule-api'
import { breakpointsTailwind, useBreakpoints, useResizeObserver, useScreenSafeArea } from '@vueuse/core'
import { DialogContent, DialogOverlay, DialogPortal, DialogRoot } from 'reka-ui'
import { DrawerContent, DrawerHandle, DrawerOverlay, DrawerPortal, DrawerRoot } from 'vaul-vue'
import { computed, onMounted, ref, watch } from 'vue'

const showDialog = defineModel<boolean>({ default: false })

const breakpoints = useBreakpoints(breakpointsTailwind)
const isDesktop = breakpoints.greaterOrEqual('md')
const screenSafeArea = useScreenSafeArea()

useResizeObserver(document.documentElement, () => screenSafeArea.update())
onMounted(() => screenSafeArea.update())

const schedules = ref<ScheduleItem[]>([])
const loading = ref(false)

// Form inputs
const newTitle = ref('')
const newRemindAt = ref('')
const newNote = ref('')
const isSubmitting = ref(false)
const formError = ref('')

const activeChatId = computed(() => {
  if (typeof window === 'undefined')
    return 0
  return Number(new URLSearchParams(window.location.search).get('chat_id') || 1001)
})

async function loadSchedules() {
  if (!activeChatId.value)
    return
  loading.value = true
  try {
    schedules.value = await fetchSchedules(activeChatId.value)
  }
  finally {
    loading.value = false
  }
}

async function handleAdd() {
  formError.value = ''
  if (!newTitle.value.trim()) {
    formError.value = '请输入提醒标题'
    return
  }
  if (!newRemindAt.value) {
    formError.value = '请选择提醒时间'
    return
  }

  isSubmitting.value = true
  try {
    let formattedTime = newRemindAt.value.replace('T', ' ')
    if (formattedTime.length === 16) {
      formattedTime += ':00'
    }

    const success = await addSchedule({
      chat_id: activeChatId.value,
      user_id: 1,
      title: newTitle.value.trim(),
      remind_at: formattedTime,
      note: newNote.value.trim(),
    })

    if (success) {
      newTitle.value = ''
      newRemindAt.value = ''
      newNote.value = ''
      await loadSchedules()
    }
    else {
      formError.value = '保存失败，请检查陪伴服务状态'
    }
  }
  catch {
    formError.value = '请求发送异常'
  }
  finally {
    isSubmitting.value = false
  }
}

async function handleDelete(scheduleId: string) {
  const success = await deleteSchedule(scheduleId)
  if (success) {
    schedules.value = schedules.value.filter(s => s.schedule_id !== scheduleId)
  }
}

watch(showDialog, (val) => {
  if (val) {
    loadSchedules()
  }
})

onMounted(() => {
  loadSchedules()
})
</script>

<template>
  <div>
    <!-- Desktop Modal Dialog (reka-ui) -->
    <DialogRoot v-if="isDesktop" :open="showDialog" @update:open="value => showDialog = value">
      <DialogPortal>
        <DialogOverlay class="fixed inset-0 z-[9999] bg-black/50 backdrop-blur-sm data-[state=closed]:animate-fadeOut data-[state=open]:animate-fadeIn" />
        <DialogContent
          class="fixed left-1/2 top-1/2 z-[9999] w-[92dvw] max-w-lg max-h-[85vh] transform overflow-y-auto rounded-2xl border border-neutral-700/60 bg-neutral-950/90 p-5 shadow-2xl backdrop-blur-xl -translate-x-1/2 -translate-y-1/2 data-[state=closed]:animate-contentHide data-[state=open]:animate-contentShow text-neutral-200 flex flex-col gap-3"
        >
          <!-- Header -->
          <div class="flex items-center justify-between border-b border-neutral-800 pb-2.5">
            <div class="flex items-center gap-2 font-semibold text-base">
              <div class="i-solar:calendar-mark-bold text-sky-400 text-lg" />
              <span>📅 校园日程提醒</span>
            </div>
            <div class="flex items-center gap-1">
              <button
                class="text-neutral-400 hover:text-sky-400 p-1 transition-colors"
                title="刷新"
                @click="loadSchedules"
              >
                <div class="i-solar:restart-bold text-sm" :class="{ 'animate-spin': loading }" />
              </button>
              <button class="text-neutral-400 hover:text-rose-400 p-1 transition-colors" title="关闭" @click="showDialog = false">
                <div class="i-solar:close-circle-bold text-base" />
              </button>
            </div>
          </div>

          <!-- Schedule List -->
          <div class="flex flex-col gap-2 max-h-60 overflow-y-auto pr-1">
            <div v-if="loading && schedules.length === 0" class="text-center py-4 text-neutral-500 font-mono text-xs">
              加载日程中...
            </div>
            <div v-else-if="schedules.length === 0" class="text-center py-4 text-neutral-500 text-xs">
              暂无定时日程提醒喵~
            </div>
            <div
              v-for="item in schedules"
              :key="item.schedule_id"
              class="flex items-start justify-between gap-2 rounded-xl border border-neutral-800/80 bg-neutral-900/60 p-2.5 transition-all hover:border-sky-500/30"
            >
              <div class="flex flex-col gap-1 min-w-0 flex-1">
                <div class="font-medium text-neutral-100 text-xs truncate">
                  {{ item.title }}
                </div>
                <div class="flex items-center gap-1.5 text-[10px] text-neutral-400 font-mono">
                  <div class="i-solar:clock-circle-bold text-sky-400" />
                  <span>{{ item.remind_at }}</span>
                </div>
                <div v-if="item.note" class="text-[11px] text-neutral-400 italic">
                  {{ item.note }}
                </div>
              </div>
              <button
                class="text-neutral-500 hover:text-rose-400 p-1 rounded transition-colors"
                title="删除日程"
                @click="handleDelete(item.schedule_id)"
              >
                <div class="i-solar:trash-bin-trash-bold text-sm" />
              </button>
            </div>
          </div>

          <!-- Add Schedule Form -->
          <div class="border-t border-neutral-800/80 pt-3 flex flex-col gap-2">
            <span class="font-semibold text-neutral-300 flex items-center gap-1 text-xs">
              <div class="i-solar:add-circle-bold text-sky-400 text-sm" />
              快速新增提醒
            </span>

            <input
              v-model="newTitle"
              type="text"
              placeholder="提醒事项（如：图书馆归还图书）"
              class="w-full rounded-lg border border-neutral-700/60 bg-neutral-900/90 px-3 py-1.5 text-xs text-neutral-100 placeholder-neutral-500 focus:border-sky-500/60 focus:outline-none"
            >

            <input
              v-model="newRemindAt"
              type="datetime-local"
              class="w-full rounded-lg border border-neutral-700/60 bg-neutral-900/90 px-3 py-1.5 text-xs text-neutral-100 focus:border-sky-500/60 focus:outline-none"
            >

            <input
              v-model="newNote"
              type="text"
              placeholder="备注说明（可选）"
              class="w-full rounded-lg border border-neutral-700/60 bg-neutral-900/90 px-3 py-1.5 text-xs text-neutral-100 placeholder-neutral-500 focus:border-sky-500/60 focus:outline-none"
            >

            <div v-if="formError" class="text-rose-400 text-xs">
              {{ formError }}
            </div>

            <button
              class="mt-1 flex items-center justify-center gap-1.5 rounded-lg bg-sky-600/80 py-1.5 font-medium text-xs text-white transition-all hover:bg-sky-500 disabled:opacity-50"
              :disabled="isSubmitting"
              @click="handleAdd"
            >
              <div class="i-solar:check-circle-bold text-sm" />
              <span>保存日程提醒</span>
            </button>
          </div>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>

    <!-- Mobile / Small Screen Bottom Drawer (vaul-vue) -->
    <DrawerRoot v-else :open="showDialog" should-scale-background @update:open="value => showDialog = value">
      <DrawerPortal>
        <DrawerOverlay class="fixed inset-0 z-[9999] bg-black/60 backdrop-blur-sm" />
        <DrawerContent
          class="fixed bottom-0 left-0 right-0 z-[9999] mt-20 flex flex-col max-h-[85vh] rounded-t-[32px] border-t border-neutral-800 bg-neutral-950/95 p-5 text-xs text-neutral-200 outline-none backdrop-blur-xl"
          :style="{ paddingBottom: `${Math.max(Number.parseFloat(screenSafeArea.bottom.value.replace('px', '')), 24)}px` }"
        >
          <DrawerHandle class="[div&]:bg-neutral-600 mb-3" />

          <!-- Header -->
          <div class="flex items-center justify-between border-b border-neutral-800 pb-3 mb-3">
            <div class="flex items-center gap-2 font-semibold text-base">
              <div class="i-solar:calendar-mark-bold text-sky-400 text-lg" />
              <span>📅 校园日程提醒</span>
            </div>
            <button
              class="text-neutral-400 hover:text-sky-400 p-1 transition-colors"
              title="刷新"
              @click="loadSchedules"
            >
              <div class="i-solar:restart-bold text-sm" :class="{ 'animate-spin': loading }" />
            </button>
          </div>

          <div class="flex flex-col gap-3 overflow-y-auto">
            <!-- Schedule List -->
            <div class="flex flex-col gap-2 max-h-52 overflow-y-auto pr-1">
              <div v-if="loading && schedules.length === 0" class="text-center py-4 text-neutral-500 font-mono text-xs">
                加载日程中...
              </div>
              <div v-else-if="schedules.length === 0" class="text-center py-4 text-neutral-500 text-xs">
                暂无定时日程提醒喵~
              </div>
              <div
                v-for="item in schedules"
                :key="item.schedule_id"
                class="flex items-start justify-between gap-2 rounded-xl border border-neutral-800/80 bg-neutral-900/60 p-2.5 transition-all hover:border-sky-500/30"
              >
                <div class="flex flex-col gap-1 min-w-0 flex-1">
                  <div class="font-medium text-neutral-100 text-xs truncate">
                    {{ item.title }}
                  </div>
                  <div class="flex items-center gap-1.5 text-[10px] text-neutral-400 font-mono">
                    <div class="i-solar:clock-circle-bold text-sky-400" />
                    <span>{{ item.remind_at }}</span>
                  </div>
                  <div v-if="item.note" class="text-[11px] text-neutral-400 italic">
                    {{ item.note }}
                  </div>
                </div>
                <button
                  class="text-neutral-500 hover:text-rose-400 p-1 rounded transition-colors"
                  title="删除日程"
                  @click="handleDelete(item.schedule_id)"
                >
                  <div class="i-solar:trash-bin-trash-bold text-sm" />
                </button>
              </div>
            </div>

            <!-- Add Schedule Form -->
            <div class="border-t border-neutral-800/80 pt-3 flex flex-col gap-2">
              <span class="font-semibold text-neutral-300 flex items-center gap-1 text-xs">
                <div class="i-solar:add-circle-bold text-sky-400 text-sm" />
                快速新增提醒
              </span>

              <input
                v-model="newTitle"
                type="text"
                placeholder="提醒事项（如：图书馆归还图书）"
                class="w-full rounded-lg border border-neutral-700/60 bg-neutral-900/90 px-3 py-1.5 text-xs text-neutral-100 placeholder-neutral-500 focus:border-sky-500/60 focus:outline-none"
              >

              <input
                v-model="newRemindAt"
                type="datetime-local"
                class="w-full rounded-lg border border-neutral-700/60 bg-neutral-900/90 px-3 py-1.5 text-xs text-neutral-100 focus:border-sky-500/60 focus:outline-none"
              >

              <input
                v-model="newNote"
                type="text"
                placeholder="备注说明（可选）"
                class="w-full rounded-lg border border-neutral-700/60 bg-neutral-900/90 px-3 py-1.5 text-xs text-neutral-100 placeholder-neutral-500 focus:border-sky-500/60 focus:outline-none"
              >

              <div v-if="formError" class="text-rose-400 text-xs">
                {{ formError }}
              </div>

              <button
                class="mt-1 flex items-center justify-center gap-1.5 rounded-lg bg-sky-600/80 py-1.5 font-medium text-xs text-white transition-all hover:bg-sky-500 disabled:opacity-50"
                :disabled="isSubmitting"
                @click="handleAdd"
              >
                <div class="i-solar:check-circle-bold text-sm" />
                <span>保存日程提醒</span>
              </button>
            </div>
          </div>
        </DrawerContent>
      </DrawerPortal>
    </DrawerRoot>
  </div>
</template>
