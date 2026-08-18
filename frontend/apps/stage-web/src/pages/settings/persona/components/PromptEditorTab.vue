<script setup lang="ts">
import { Button } from '@proj-airi/ui'
import { computed, ref, watch } from 'vue'

import { compileBasePrompt, usePersonaStore } from '@proj-airi/stage-ui/stores/persona'

const personaStore = usePersonaStore()

const basePrompt = ref('')
const sleepyPrompt = ref('')
const isSaving = ref(false)
const saveNotice = ref<{ type: 'success' | 'warn'; msg: string } | null>(null)

watch(
  () => personaStore.mergedPersona,
  (merged) => {
    basePrompt.value = merged.base_prompt || ''
    sleepyPrompt.value = merged.sleepy_prompt || ''
  },
  { immediate: true },
)

const compiledPreview = computed(() => {
  return compileBasePrompt(basePrompt.value, personaStore.localOverrides)
})

async function handleSave() {
  isSaving.value = true
  saveNotice.value = null
  try {
    const res = await personaStore.savePersona({
      base_prompt: basePrompt.value,
      sleepy_prompt: sleepyPrompt.value,
    })
    if (res.isRemoteSynced) {
      saveNotice.value = {
        type: 'success',
        msg: '人设已成功同步至 Admin 后台及内存 (8094/WS 200 OK)',
      }
    }
    else {
      saveNotice.value = {
        type: 'warn',
        msg: 'Admin 服务 (8094) 未打通或响应异常，修改已在前端与 WS 内存中生效',
      }
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

    <!-- Base Prompt Editor -->
    <div class="flex flex-col gap-2">
      <div class="flex items-center justify-between">
        <label class="text-sm font-medium text-neutral-800 dark:text-neutral-200">
          Base System Prompt (基础提示词)
        </label>
        <span class="text-xs text-neutral-400">
          {{ basePrompt.length }} 字符
        </span>
      </div>
      <textarea
        v-model="basePrompt"
        rows="10"
        class="w-full resize-y rounded-xl border border-neutral-200 bg-neutral-50/50 p-4 text-sm font-mono text-neutral-900 transition-colors focus:border-primary-500 focus:outline-none dark:border-neutral-800 dark:bg-neutral-900/40 dark:text-neutral-100"
        placeholder="输入猫娘的核心 Base Prompt..."
      />
    </div>

    <!-- Sleepy Prompt Editor (Collapsible) -->
    <details class="group rounded-xl border border-neutral-200 bg-neutral-50/30 dark:border-neutral-800 dark:bg-neutral-900/20">
      <summary class="flex cursor-pointer items-center justify-between p-4 font-medium text-sm text-neutral-700 dark:text-neutral-300">
        <div class="flex items-center gap-2">
          <div class="i-solar:moon-stars-bold text-primary-500" />
          <span>Sleepy Prompt (迷糊/睡眠状态提示词)</span>
        </div>
        <div class="i-solar:alt-arrow-down-bold transition-transform group-open:rotate-180" />
      </summary>
      <div class="p-4 pt-0">
        <textarea
          v-model="sleepyPrompt"
          rows="5"
          class="w-full resize-y rounded-lg border border-neutral-200 bg-neutral-50/50 p-3 text-sm font-mono text-neutral-900 focus:border-primary-500 focus:outline-none dark:border-neutral-800 dark:bg-neutral-900/40 dark:text-neutral-100"
          placeholder="输入半梦半醒迷糊状态的 Prompt..."
        />
      </div>
    </details>

    <!-- Real-time Compiled Prompt Preview -->
    <div class="flex flex-col gap-2 rounded-xl border border-neutral-200/80 bg-neutral-100/60 p-4 dark:border-neutral-800/80 dark:bg-neutral-950/60">
      <div class="flex items-center justify-between text-xs text-neutral-500">
        <span class="font-semibold flex items-center gap-1">
          <div class="i-solar:eye-bold text-primary-500" />
          实时编译结果 (包含前端称呼、语气词与滑块注入)
        </span>
        <span>{{ compiledPreview.length }} 字符</span>
      </div>
      <pre class="max-h-48 overflow-y-auto whitespace-pre-wrap font-mono text-xs text-neutral-600 dark:text-neutral-400">{{ compiledPreview }}</pre>
    </div>

    <!-- Submit Action -->
    <div class="flex justify-end pt-2">
      <Button :disabled="isSaving" class="px-6" @click="handleSave">
        <div v-if="isSaving" class="i-svg-spinners:90-ring-with-bg mr-2 text-base" />
        <div v-else class="i-solar:diskette-bold mr-2 text-base" />
        保存人设并热更新
      </Button>
    </div>
  </div>
</template>
