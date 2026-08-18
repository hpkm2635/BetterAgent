<script setup lang="ts">
import { Button, FieldInput } from '@proj-airi/ui'
import { ref, watch } from 'vue'

import { usePersonaStore } from '@proj-airi/stage-ui/stores/persona'

const personaStore = usePersonaStore()

const name = ref('')
const userCallsign = ref('')
const catchphraseInput = ref('')
const catchphrases = ref<string[]>([])
const isSaving = ref(false)
const saveNotice = ref<{ type: 'success' | 'warn'; msg: string } | null>(null)

watch(
  () => personaStore.mergedPersona,
  (merged) => {
    name.value = merged.name || ''
    userCallsign.value = merged.overrides.userCallsign || '主人'
    catchphrases.value = [...(merged.overrides.catchphrases || ['喵~', '呜咪~', '哼'])]
  },
  { immediate: true },
)

function addCatchphrase() {
  const val = catchphraseInput.value.trim()
  if (val && !catchphrases.value.includes(val)) {
    catchphrases.value.push(val)
    catchphraseInput.value = ''
  }
}

function removeCatchphrase(tag: string) {
  catchphrases.value = catchphrases.value.filter(t => t !== tag)
}

async function handleSave() {
  isSaving.value = true
  saveNotice.value = null
  try {
    const res = await personaStore.savePersona(
      { name: name.value },
      {
        userCallsign: userCallsign.value,
        catchphrases: catchphrases.value,
      },
    )
    if (res.isRemoteSynced) {
      saveNotice.value = { type: 'success', msg: '基础人设已更新至 Admin 后台与本地存储' }
    }
    else {
      saveNotice.value = { type: 'warn', msg: 'Admin 服务 (8094) 未打通，改动已在本地与内存中生效' }
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

    <!-- Character Name -->
    <div class="flex flex-col gap-2">
      <label class="text-sm font-medium text-neutral-800 dark:text-neutral-200">
        猫娘角色名称 (Name)
      </label>
      <FieldInput
        v-model="name"
        placeholder="如: Camelia"
        class="w-full"
      />
      <p class="text-xs text-neutral-400">
        此名称将同步至 Admin API (8094) `name` 字段与底层 `catgirl.yaml`。
      </p>
    </div>

    <!-- User Callsign -->
    <div class="flex flex-col gap-2">
      <label class="text-sm font-medium text-neutral-800 dark:text-neutral-200">
        用户称呼偏好 (User Callsign)
      </label>
      <FieldInput
        v-model="userCallsign"
        placeholder="如: 主人 / 学长 / 哥哥"
        class="w-full"
      />
      <p class="text-xs text-neutral-400">
        猫娘对话时称呼你的词汇，将自动注入 System Prompt 头部。
      </p>
    </div>

    <!-- Catchphrases Tag Input -->
    <div class="flex flex-col gap-2">
      <label class="text-sm font-medium text-neutral-800 dark:text-neutral-200">
        自定义语气助词 / 句尾词池 (Catchphrases)
      </label>

      <div class="flex flex-wrap gap-2 rounded-xl border border-neutral-200 bg-neutral-50/50 p-3 dark:border-neutral-800 dark:bg-neutral-900/40">
        <span
          v-for="tag in catchphrases"
          :key="tag"
          class="inline-flex items-center gap-1.5 rounded-lg bg-primary-500/10 px-2.5 py-1 text-xs font-medium text-primary-600 dark:text-primary-400"
        >
          {{ tag }}
          <button class="hover:text-primary-800 dark:hover:text-primary-200" @click="removeCatchphrase(tag)">
            <div class="i-solar:close-circle-bold text-xs" />
          </button>
        </span>

        <div class="flex flex-1 items-center min-w-140px">
          <input
            v-model="catchphraseInput"
            type="text"
            placeholder="输入新语气词按 Enter 添加..."
            class="w-full bg-transparent text-xs text-neutral-900 focus:outline-none dark:text-neutral-100"
            @keydown.enter.prevent="addCatchphrase"
          >
          <Button class="!py-0.5 !px-2 text-xs" @click="addCatchphrase">
            添加
          </Button>
        </div>
      </div>
    </div>

    <!-- Submit Action -->
    <div class="flex justify-end pt-2">
      <Button :disabled="isSaving" class="px-6" @click="handleSave">
        <div v-if="isSaving" class="i-svg-spinners:90-ring-with-bg mr-2 text-base" />
        <div v-else class="i-solar:diskette-bold mr-2 text-base" />
        保存基础设定
      </Button>
    </div>
  </div>
</template>
