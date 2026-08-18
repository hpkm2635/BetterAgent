<script setup lang="ts">
import { Button } from '@proj-airi/ui'
import { computed, ref, watch } from 'vue'

import { usePersonaStore } from '@proj-airi/stage-ui/stores/persona'

const personaStore = usePersonaStore()

const tsundereWeight = ref(70)
const clingyWeight = ref(60)
const isSaving = ref(false)
const saveNotice = ref<{ type: 'success' | 'warn'; msg: string } | null>(null)

watch(
  () => personaStore.mergedPersona,
  (merged) => {
    tsundereWeight.value = merged.overrides.tsundereWeight ?? 70
    clingyWeight.value = merged.overrides.clingyWeight ?? 60
  },
  { immediate: true },
)

const personalityDescription = computed(() => {
  if (tsundereWeight.value > 75 && clingyWeight.value > 75) {
    return '【超级傲娇重度粘人】：嘴上极其毒舌/高傲，但时刻黏在主人身边不肯离开。'
  }
  if (tsundereWeight.value > 70) {
    return '【典型傲娇猫娘】：面对称赞嘴硬反驳（才没有很高兴呢喵！），但眼神暗藏欢喜。'
  }
  if (clingyWeight.value > 70) {
    return '【贴贴直球猫娘】：性格温顺依赖，喜欢主动索要安抚与抱抱。'
  }
  return '【均衡性格猫娘】：傲娇与粘人比例适中，自然随性。'
})

async function handleSave() {
  isSaving.value = true
  saveNotice.value = null
  try {
    const res = await personaStore.savePersona(
      {},
      {
        tsundereWeight: tsundereWeight.value,
        clingyWeight: clingyWeight.value,
      },
    )
    if (res.isRemoteSynced) {
      saveNotice.value = { type: 'success', msg: '性格权重已成功编译并同步至 Admin 后台' }
    }
    else {
      saveNotice.value = { type: 'warn', msg: 'Admin 服务 (8094) 未打通，性格权重已在前端与 WS 内存中生效' }
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

    <!-- Tsundere Weight Slider -->
    <div class="flex flex-col gap-2 rounded-xl border border-neutral-200 bg-neutral-50/50 p-4 dark:border-neutral-800 dark:bg-neutral-900/40">
      <div class="flex items-center justify-between">
        <label class="text-sm font-medium text-neutral-800 dark:text-neutral-200 flex items-center gap-2">
          <div class="i-solar:fire-bold text-amber-500" />
          傲娇系数 (Tsundere Weight)
        </label>
        <span class="text-xs font-mono font-semibold text-amber-500">{{ tsundereWeight }}%</span>
      </div>
      <input
        v-model.number="tsundereWeight"
        type="range"
        min="0"
        max="100"
        step="5"
        class="h-2 w-full cursor-pointer accent-amber-500"
      >
      <div class="flex justify-between text-xs text-neutral-400">
        <span>直率与坦诚 (0%)</span>
        <span>典型傲娇 (50%)</span>
        <span>重度嘴硬 (100%)</span>
      </div>
    </div>

    <!-- Clingy Weight Slider -->
    <div class="flex flex-col gap-2 rounded-xl border border-neutral-200 bg-neutral-50/50 p-4 dark:border-neutral-800 dark:bg-neutral-900/40">
      <div class="flex items-center justify-between">
        <label class="text-sm font-medium text-neutral-800 dark:text-neutral-200 flex items-center gap-2">
          <div class="i-solar:heart-bold text-rose-500" />
          粘人与依赖度 (Clingy Weight)
        </label>
        <span class="text-xs font-mono font-semibold text-rose-500">{{ clingyWeight }}%</span>
      </div>
      <input
        v-model.number="clingyWeight"
        type="range"
        min="0"
        max="100"
        step="5"
        class="h-2 w-full cursor-pointer accent-rose-500"
      >
      <div class="flex justify-between text-xs text-neutral-400">
        <span>独立高冷 (0%)</span>
        <span>适度粘人 (50%)</span>
        <span>寸步不离 (100%)</span>
      </div>
    </div>

    <!-- Real-time Personality Summary Box -->
    <div class="rounded-xl border border-primary-500/20 bg-primary-500/5 p-4 text-xs text-primary-700 dark:text-primary-300">
      <div class="font-semibold mb-1 flex items-center gap-1.5">
        <div class="i-solar:magic-stick-3-bold text-sm" />
        性格倾向实时预判
      </div>
      <p>{{ personalityDescription }}</p>
    </div>

    <!-- Submit Action -->
    <div class="flex justify-end pt-2">
      <Button :disabled="isSaving" class="px-6" @click="handleSave">
        <div v-if="isSaving" class="i-svg-spinners:90-ring-with-bg mr-2 text-base" />
        <div v-else class="i-solar:diskette-bold mr-2 text-base" />
        保存性格权重
      </Button>
    </div>
  </div>
</template>
