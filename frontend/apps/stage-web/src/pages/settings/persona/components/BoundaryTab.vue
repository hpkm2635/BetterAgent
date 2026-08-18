<script setup lang="ts">
import { Button } from '@proj-airi/ui'
import { ref, watch } from 'vue'

import { usePersonaStore } from '@proj-airi/stage-ui/stores/persona'

const personaStore = usePersonaStore()

const knowledgeScope = ref('')
const forbiddenInput = ref('')
const forbiddenTopics = ref<string[]>([])
const campusKbEnabled = ref(true)
const maxReplyLength = ref(500)
const isSaving = ref(false)
const saveNotice = ref<{ type: 'success' | 'warn'; msg: string } | null>(null)

watch(
  () => personaStore.mergedPersona,
  (merged) => {
    knowledgeScope.value = merged.knowledge_scope || ''
    const rawForbidden = merged.forbidden_topics || ''
    forbiddenTopics.value = rawForbidden ? rawForbidden.split(/[,,\n]/).map(s => s.trim()).filter(Boolean) : []
    campusKbEnabled.value = merged.overrides.campusKbEnabled ?? true
    maxReplyLength.value = merged.overrides.maxReplyLength ?? 500
  },
  { immediate: true },
)

function addForbiddenTopic() {
  const val = forbiddenInput.value.trim()
  if (val && !forbiddenTopics.value.includes(val)) {
    forbiddenTopics.value.push(val)
    forbiddenInput.value = ''
  }
}

function removeForbiddenTopic(tag: string) {
  forbiddenTopics.value = forbiddenTopics.value.filter(t => t !== tag)
}

async function handleSave() {
  isSaving.value = true
  saveNotice.value = null
  try {
    const res = await personaStore.savePersona(
      {
        knowledge_scope: knowledgeScope.value,
        forbidden_topics: forbiddenTopics.value.join(','),
      },
      {
        campusKbEnabled: campusKbEnabled.value,
        maxReplyLength: maxReplyLength.value,
      },
    )
    if (res.isRemoteSynced) {
      saveNotice.value = { type: 'success', msg: '交互边界与知识范围已保存至 Admin 后台' }
    }
    else {
      saveNotice.value = { type: 'warn', msg: 'Admin 服务 (8094) 未打通，设置已在本地与内存中生效' }
    }
  }
  finally {
    isSaving.value = false
  }
}
</script>

<template>
  <div class="flex flex-col gap-6">
    <!-- Save Status Banner -->
    <div
      v-if="saveNotice"
      class="rounded-lg p-3 text-sm flex items-center justify-between"
      :class="saveNotice.type === 'success'
        ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20'
        : 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20'"
    >
      <div class="flex items-center gap-2">
        <div :class="saveNotice.type === 'success' ? 'i-solar:check-circle-bold' : 'i-solar:danger-triangle-bold'" class="text-base" />
        <span>{{ saveNotice.msg }}</span>
      </div>
      <button class="opacity-60 hover:opacity-100" @click="saveNotice = null">
        <div class="i-solar:close-circle-bold text-base" />
      </button>
    </div>

    <!-- Campus KB Toggle Switch -->
    <div class="flex items-center justify-between rounded-xl border border-neutral-200 bg-neutral-50/50 p-4 dark:border-neutral-800 dark:bg-neutral-900/40">
      <div>
        <div class="font-medium text-sm text-neutral-800 dark:text-neutral-200 flex items-center gap-2">
          <div class="i-solar:book-bookmark-bold text-primary-500" />
          校园知识库 RAG 检索 (Campus KB)
        </div>
        <div class="text-xs text-neutral-400 mt-0.5">
          开启后猫娘会自动检索校园 FAQ、图书馆与规章制度。
        </div>
      </div>
      <label class="relative inline-flex cursor-pointer items-center">
        <input v-model="campusKbEnabled" type="checkbox" class="peer sr-only">
        <div class="h-6 w-11 rounded-full bg-neutral-300 transition-colors peer-checked:bg-primary-500 dark:bg-neutral-700 peer-focus:outline-none after:absolute after:left-0.5 after:top-0.5 after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all after:content-[''] peer-checked:after:translate-x-5" />
      </label>
    </div>

    <!-- Knowledge Scope Textarea -->
    <div class="flex flex-col gap-2">
      <label class="text-sm font-medium text-neutral-800 dark:text-neutral-200">
        知识域约束 (Knowledge Scope)
      </label>
      <textarea
        v-model="knowledgeScope"
        rows="4"
        class="w-full resize-y rounded-xl border border-neutral-200 bg-neutral-50/50 p-4 text-sm font-mono text-neutral-900 transition-colors focus:border-primary-500 focus:outline-none dark:border-neutral-800 dark:bg-neutral-900/40 dark:text-neutral-100"
        placeholder="限定猫娘擅长或了解的特定专业知识或对话范畴..."
      />
    </div>

    <!-- Forbidden Topics Tag Input -->
    <div class="flex flex-col gap-2">
      <label class="text-sm font-medium text-neutral-800 dark:text-neutral-200">
        禁忌与回避话题 (Forbidden Topics)
      </label>
      <div class="flex flex-wrap gap-2 rounded-xl border border-neutral-200 bg-neutral-50/50 p-3 dark:border-neutral-800 dark:bg-neutral-900/40">
        <span
          v-for="tag in forbiddenTopics"
          :key="tag"
          class="inline-flex items-center gap-1.5 rounded-lg bg-red-500/10 px-2.5 py-1 text-xs font-medium text-red-600 dark:text-red-400"
        >
          {{ tag }}
          <button class="hover:text-red-800 dark:hover:text-red-200" @click="removeForbiddenTopic(tag)">
            <div class="i-solar:close-circle-bold text-xs" />
          </button>
        </span>

        <div class="flex flex-1 items-center min-w-160px">
          <input
            v-model="forbiddenInput"
            type="text"
            placeholder="输入禁忌话题按 Enter 添加..."
            class="w-full bg-transparent text-xs text-neutral-900 focus:outline-none dark:text-neutral-100"
            @keydown.enter.prevent="addForbiddenTopic"
          >
          <Button class="!py-0.5 !px-2 text-xs" @click="addForbiddenTopic">
            添加
          </Button>
        </div>
      </div>
    </div>

    <!-- Max Reply Length Slider -->
    <div class="flex flex-col gap-2 rounded-xl border border-neutral-200 bg-neutral-50/50 p-4 dark:border-neutral-800 dark:bg-neutral-900/40">
      <div class="flex items-center justify-between">
        <label class="text-sm font-medium text-neutral-800 dark:text-neutral-200">
          单次回复最大字符限制 (Max Reply Length)
        </label>
        <span class="text-xs font-mono font-semibold text-primary-500">{{ maxReplyLength }} 字符</span>
      </div>
      <input
        v-model.number="maxReplyLength"
        type="range"
        min="100"
        max="1500"
        step="50"
        class="h-2 w-full cursor-pointer accent-primary-500"
      >
      <p class="text-xs text-neutral-400">
        防止猫娘长篇大论，保持二次元伴侣聊天节奏。
      </p>
    </div>

    <!-- Submit Action -->
    <div class="flex justify-end pt-2">
      <Button :disabled="isSaving" class="px-6" @click="handleSave">
        <div v-if="isSaving" class="i-svg-spinners:90-ring-with-bg mr-2 text-base" />
        <div v-else class="i-solar:diskette-bold mr-2 text-base" />
        保存交互边界
      </Button>
    </div>
  </div>
</template>
