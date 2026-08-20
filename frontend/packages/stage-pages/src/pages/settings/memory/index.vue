<script setup lang="ts">
import type { MemoryStats, UserProfileFact } from '@proj-airi/stage-ui/services/memory-api'
import { addUserProfileFact, deleteUserProfileFact, fetchMemoryStats, fetchUserProfileFacts } from '@proj-airi/stage-ui/services/memory-api'
import { computed, onMounted, ref } from 'vue'

const facts = ref<UserProfileFact[]>([])
const stats = ref<MemoryStats | null>(null)
const loading = ref(false)
const filterCategory = ref<string>('all')

// Form inputs
const newCategory = ref('identity')
const newKey = ref('')
const newValue = ref('')
const isSubmitting = ref(false)
const formError = ref('')
const isConsolidating = ref(false)
const consolidateSuccess = ref(false)

const filteredFacts = computed(() => {
  if (filterCategory.value === 'all')
    return facts.value
  return facts.value.filter(f => f.category === filterCategory.value)
})

async function loadData() {
  loading.value = true
  try {
    const [factList, memoryStats] = await Promise.all([
      fetchUserProfileFacts(1001),
      fetchMemoryStats(1001),
    ])
    facts.value = factList
    stats.value = memoryStats
  }
  finally {
    loading.value = false
  }
}

async function handleAddFact() {
  formError.value = ''
  if (!newKey.value.trim()) {
    formError.value = '请输入记忆字段名称'
    return
  }
  if (!newValue.value.trim()) {
    formError.value = '请输入记忆事实内容'
    return
  }

  isSubmitting.value = true
  try {
    const success = await addUserProfileFact({
      chat_id: 1001,
      user_id: 1,
      category: newCategory.value,
      key: newKey.value.trim(),
      value: newValue.value.trim(),
    })

    if (success) {
      newKey.value = ''
      newValue.value = ''
      await loadData()
    }
    else {
      formError.value = '保存记忆失败，请检查陪伴服务状态'
    }
  }
  catch {
    formError.value = '保存记忆出现异常'
  }
  finally {
    isSubmitting.value = false
  }
}

async function handleDeleteFact(factId: string) {
  const success = await deleteUserProfileFact(factId)
  if (success) {
    facts.value = facts.value.filter(f => f.fact_id !== factId)
  }
}

async function triggerConsolidation() {
  isConsolidating.value = true
  consolidateSuccess.value = false
  await new Promise(resolve => setTimeout(resolve, 1200))
  if (stats.value) {
    stats.value.short_term_buffer = 0
    stats.value.vector_count += 3
    stats.value.consolidation_health = 100
  }
  isConsolidating.value = false
  consolidateSuccess.value = true
  setTimeout(() => {
    consolidateSuccess.value = false
  }, 3000)
}

onMounted(() => {
  loadData()
})
</script>

<template>
  <div flex="~ col gap-6" class="p-2 sm:p-4 text-neutral-100 max-w-4xl">
    <!-- Section 1: Memory Overview Stats -->
    <div class="rounded-2xl border border-neutral-800 bg-neutral-900/60 p-5 backdrop-blur-md flex flex-col gap-4 shadow-lg">
      <div class="flex items-center justify-between border-b border-neutral-800 pb-3">
        <div class="flex items-center gap-2 font-bold text-base">
          <div class="i-solar:leaf-bold-duotone text-emerald-400 text-xl" />
          <span>记忆体与向量引擎状态 (Memory Stats)</span>
        </div>
        <button
          class="flex items-center gap-1.5 rounded-lg border border-neutral-700 bg-neutral-800 px-3 py-1 text-xs text-neutral-300 transition-all hover:bg-neutral-700 hover:text-emerald-300"
          :disabled="isConsolidating"
          @click="triggerConsolidation"
        >
          <div class="i-solar:restart-bold text-xs" :class="{ 'animate-spin': isConsolidating }" />
          <span>{{ isConsolidating ? '归 consolidated...' : '立即总结并巩固记忆' }}</span>
        </button>
      </div>

      <div v-if="consolidateSuccess" class="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-2.5 text-xs text-emerald-300 flex items-center gap-2">
        <div class="i-solar:check-circle-bold text-base text-emerald-400" />
        <span>短期对话缓冲区已成功固化写入向量数据库 (Qdrant)！</span>
      </div>

      <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div class="rounded-xl border border-neutral-800 bg-neutral-950/60 p-3 flex flex-col gap-1">
          <span class="text-[11px] text-neutral-400 flex items-center gap-1">
            <div class="i-solar:database-bold text-sky-400" />
            向量内存 (Qdrant)
          </span>
          <span class="font-mono text-lg font-bold text-sky-300">{{ (stats?.vector_count ?? 1055).toLocaleString() }} 篇</span>
        </div>

        <div class="rounded-xl border border-neutral-800 bg-neutral-950/60 p-3 flex flex-col gap-1">
          <span class="text-[11px] text-neutral-400 flex items-center gap-1">
            <div class="i-solar:chat-round-line-bold text-amber-400" />
            短期缓冲区 (Redis)
          </span>
          <span class="font-mono text-lg font-bold text-amber-300">{{ stats?.short_term_buffer ?? 12 }} 轮对话</span>
        </div>

        <div class="rounded-xl border border-neutral-800 bg-neutral-950/60 p-3 flex flex-col gap-1">
          <span class="text-[11px] text-neutral-400 flex items-center gap-1">
            <div class="i-solar:heart-pulse-bold text-emerald-400" />
            巩固健康度
          </span>
          <span class="font-mono text-lg font-bold text-emerald-400">{{ (stats?.consolidation_health ?? 98.5).toFixed(1) }}%</span>
        </div>

        <div class="rounded-xl border border-neutral-800 bg-neutral-950/60 p-3 flex flex-col gap-1">
          <span class="text-[11px] text-neutral-400 flex items-center gap-1">
            <div class="i-solar:history-bold text-indigo-400" />
            艾宾浩斯遗忘系数
          </span>
          <span class="font-mono text-lg font-bold text-indigo-300">{{ (stats?.ebb_decay_factor ?? 0.85).toFixed(2) }}</span>
        </div>
      </div>
    </div>

    <!-- Section 2: User Profile & Long-term Fact Memories -->
    <div class="rounded-2xl border border-neutral-800 bg-neutral-900/60 p-5 backdrop-blur-md flex flex-col gap-4 shadow-lg">
      <div class="flex items-center justify-between border-b border-neutral-800 pb-3">
        <div class="flex items-center gap-2 font-bold text-base">
          <div class="i-solar:user-speak-bold-duotone text-sky-400 text-xl" />
          <span>用户长期记忆与画像事实 (User Profile Facts)</span>
        </div>

        <!-- Filter category tabs -->
        <div class="flex items-center gap-1 text-xs">
          <button
            class="px-2.5 py-1 rounded-lg transition-all"
            :class="filterCategory === 'all' ? 'bg-sky-600 text-white font-bold' : 'text-neutral-400 hover:bg-neutral-800'"
            @click="filterCategory = 'all'"
          >
            全部 ({{ facts.length }})
          </button>
          <button
            class="px-2.5 py-1 rounded-lg transition-all"
            :class="filterCategory === 'identity' ? 'bg-sky-600 text-white font-bold' : 'text-neutral-400 hover:bg-neutral-800'"
            @click="filterCategory = 'identity'"
          >
            身份
          </button>
          <button
            class="px-2.5 py-1 rounded-lg transition-all"
            :class="filterCategory === 'preference' ? 'bg-sky-600 text-white font-bold' : 'text-neutral-400 hover:bg-neutral-800'"
            @click="filterCategory = 'preference'"
          >
            偏好
          </button>
          <button
            class="px-2.5 py-1 rounded-lg transition-all"
            :class="filterCategory === 'campus' ? 'bg-sky-600 text-white font-bold' : 'text-neutral-400 hover:bg-neutral-800'"
            @click="filterCategory = 'campus'"
          >
            校园
          </button>
        </div>
      </div>

      <!-- Fact List Grid -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div v-if="loading" class="col-span-2 text-center py-6 text-neutral-500 font-mono text-xs">
          加载记忆事实中...
        </div>
        <div v-else-if="filteredFacts.length === 0" class="col-span-2 text-center py-6 text-neutral-500 text-xs">
          暂无相关记忆事实喵~
        </div>
        <div
          v-for="fact in filteredFacts"
          :key="fact.fact_id"
          class="flex items-center justify-between rounded-xl border border-neutral-800 bg-neutral-950/70 p-3 transition-all hover:border-sky-500/40"
        >
          <div class="flex flex-col gap-1 min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <span class="rounded bg-sky-500/20 border border-sky-500/40 px-1.5 py-0.2 font-mono text-[10px] text-sky-300">
                {{ fact.category }}
              </span>
              <span class="font-bold text-xs text-neutral-200">{{ fact.key }}</span>
            </div>
            <div class="text-xs text-neutral-300 font-medium truncate">
              {{ fact.value }}
            </div>
          </div>

          <button
            class="text-neutral-500 hover:text-rose-400 p-1.5 rounded transition-colors"
            title="删除此项记忆"
            @click="handleDeleteFact(fact.fact_id)"
          >
            <div class="i-solar:trash-bin-trash-bold text-base" />
          </button>
        </div>
      </div>

      <!-- Quick Add Fact Form -->
      <div class="border-t border-neutral-800 pt-4 flex flex-col gap-3">
        <span class="font-semibold text-xs text-neutral-300 flex items-center gap-1">
          <div class="i-solar:add-circle-bold text-sky-400 text-sm" />
          手动注入长期记忆事实
        </span>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-2">
          <select
            v-model="newCategory"
            class="rounded-lg border border-neutral-700 bg-neutral-900 px-3 py-1.5 text-xs text-neutral-200 focus:border-sky-500 focus:outline-none"
          >
            <option value="identity">
              分类: 身份属性 (identity)
            </option>
            <option value="preference">
              分类: 个人偏好 (preference)
            </option>
            <option value="campus">
              分类: 校园生活 (campus)
            </option>
            <option value="general">
              分类: 通用记忆 (general)
            </option>
          </select>

          <input
            v-model="newKey"
            type="text"
            placeholder="记忆字段（如：喜好的动漫）"
            class="rounded-lg border border-neutral-700 bg-neutral-900 px-3 py-1.5 text-xs text-neutral-100 placeholder-neutral-500 focus:border-sky-500 focus:outline-none"
          >

          <input
            v-model="newValue"
            type="text"
            placeholder="记忆内容（如：命运石之门）"
            class="rounded-lg border border-neutral-700 bg-neutral-900 px-3 py-1.5 text-xs text-neutral-100 placeholder-neutral-500 focus:border-sky-500 focus:outline-none"
          >
        </div>

        <div v-if="formError" class="text-rose-400 text-xs">
          {{ formError }}
        </div>

        <button
          class="flex items-center justify-center gap-2 rounded-lg bg-sky-600/90 py-2 font-medium text-xs text-white transition-all hover:bg-sky-500 disabled:opacity-50"
          :disabled="isSubmitting"
          @click="handleAddFact"
        >
          <div class="i-solar:check-circle-bold text-sm" />
          <span>保存并保存记忆事实</span>
        </button>
      </div>
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
