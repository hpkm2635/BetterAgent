<script setup lang="ts">
import { computed, ref } from 'vue'

import { useBetterAgentGatewayStore } from '@proj-airi/stage-ui/stores/modules/betteragent-gateway'

const gatewayStore = useBetterAgentGatewayStore()

const isOpen = ref(true)
const isClosed = ref(false)

const emoState = computed(() => gatewayStore.emotionalState)

const moodColorClass = computed(() => {
  const mood = emoState.value?.mood || 'NEUTRAL'
  switch (mood) {
    case 'HAPPY':
      return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40'
    case 'MOODY':
      return 'bg-amber-500/20 text-amber-400 border-amber-500/40'
    case 'SLEEPY':
      return 'bg-indigo-500/20 text-indigo-400 border-indigo-500/40'
    case 'JEALOUS':
      return 'bg-rose-500/20 text-rose-400 border-rose-500/40 animate-pulse'
    default:
      return 'bg-neutral-500/20 text-neutral-400 border-neutral-500/40'
  }
})

const affectionPercent = computed(() => {
  const val = emoState.value?.affection ?? 50
  return Math.max(0, Math.min(100, val))
})

const energyPercent = computed(() => {
  const val = (emoState.value?.energy ?? 0.8) * 100
  return Math.max(0, Math.min(100, val))
})

const batteryPercent = computed(() => {
  const val = (emoState.value?.social_battery ?? 0.9) * 100
  return Math.max(0, Math.min(100, val))
})
</script>

<template>
  <div v-if="!isClosed" class="fixed top-4 right-4 z-50 flex flex-col items-end gap-2 pointer-events-auto select-none">
    <!-- Toggle Button -->
    <div class="flex items-center gap-1">
      <button
        class="flex items-center gap-2 rounded-full border border-neutral-700/60 bg-neutral-900/80 px-3 py-1.5 text-xs text-neutral-200 shadow-lg backdrop-blur-md transition-all hover:border-primary-500/60 hover:bg-neutral-900"
        @click="isOpen = !isOpen"
      >
        <div class="i-solar:heart-pulse-bold text-rose-500 text-sm" />
        <span class="font-medium">猫娘状态 HUD</span>
        <div :class="isOpen ? 'i-solar:alt-arrow-up-bold' : 'i-solar:alt-arrow-down-bold'" class="text-xs opacity-60" />
      </button>

      <button
        class="flex items-center justify-center rounded-full border border-neutral-700/60 bg-neutral-900/80 p-1.5 text-xs text-neutral-400 shadow-lg backdrop-blur-md transition-all hover:text-rose-400 hover:border-rose-500/60 hover:bg-neutral-900"
        title="关闭 HUD"
        @click="isClosed = true"
      >
        <div class="i-solar:close-circle-bold text-sm" />
      </button>
    </div>

    <!-- Floating HUD Card -->
    <Transition
      enter-active-class="transition-all duration-300 ease-out"
      enter-from-class="opacity-0 translate-y--2 scale-95"
      enter-to-class="opacity-100 translate-y-0 scale-100"
      leave-active-class="transition-all duration-200 ease-in"
      leave-from-class="opacity-100 translate-y-0 scale-100"
      leave-to-class="opacity-0 translate-y--2 scale-95"
    >
      <div
        v-if="isOpen"
        class="w-72 rounded-2xl border border-neutral-700/50 bg-neutral-950/85 p-4 text-xs shadow-2xl backdrop-blur-xl text-neutral-200 flex flex-col gap-3"
      >
        <!-- Header & Mood Badge -->
        <div class="flex items-center justify-between border-b border-neutral-800 pb-2">
          <div class="flex items-center gap-2 font-semibold text-sm">
            <div class="i-solar:cat-bold text-primary-400" />
            <span>Camelia 情绪指标</span>
          </div>
          <div class="flex items-center gap-2">
            <span class="rounded-full border px-2 py-0.5 font-mono text-[10px] font-bold" :class="moodColorClass">
              {{ emoState?.mood || 'NEUTRAL' }}
            </span>
            <button class="text-neutral-400 hover:text-rose-400" title="关闭" @click="isClosed = true">
              <div class="i-solar:close-circle-bold text-sm" />
            </button>
          </div>
        </div>

        <!-- Affection Bar -->
        <div class="flex flex-col gap-1">
          <div class="flex justify-between text-neutral-400">
            <span class="flex items-center gap-1">
              <div class="i-solar:heart-bold text-rose-500" />
              好感度 (Affection)
            </span>
            <span class="font-mono text-rose-400 font-bold">{{ affectionPercent.toFixed(1) }} / 100</span>
          </div>
          <div class="h-2 w-full overflow-hidden rounded-full bg-neutral-800">
            <div
              class="h-full rounded-full bg-gradient-to-r from-rose-500 to-pink-400 transition-all duration-500"
              :style="{ width: `${affectionPercent}%` }"
            />
          </div>
        </div>

        <!-- Energy & Social Battery -->
        <div class="grid grid-cols-2 gap-3">
          <!-- Energy -->
          <div class="flex flex-col gap-1">
            <div class="flex justify-between text-neutral-400 text-[11px]">
              <span class="flex items-center gap-1">
                <div class="i-solar:bolt-bold text-amber-400" />
                精力
              </span>
              <span class="font-mono text-amber-400 font-semibold">{{ energyPercent.toFixed(0) }}%</span>
            </div>
            <div class="h-1.5 w-full overflow-hidden rounded-full bg-neutral-800">
              <div
                class="h-full rounded-full bg-amber-400 transition-all duration-500"
                :style="{ width: `${energyPercent}%` }"
              />
            </div>
          </div>

          <!-- Social Battery -->
          <div class="flex flex-col gap-1">
            <div class="flex justify-between text-neutral-400 text-[11px]">
              <span class="flex items-center gap-1">
                <div class="i-solar:battery-charge-bold text-emerald-400" />
                社交电量
              </span>
              <span class="font-mono text-emerald-400 font-semibold">{{ batteryPercent.toFixed(0) }}%</span>
            </div>
            <div class="h-1.5 w-full overflow-hidden rounded-full bg-neutral-800">
              <div
                class="h-full rounded-full bg-emerald-400 transition-all duration-500"
                :style="{ width: `${batteryPercent}%` }"
              />
            </div>
          </div>
        </div>

        <!-- VAD Coordinates & Jealous Status -->
        <div class="flex items-center justify-between border-t border-neutral-800/80 pt-2 text-[10px] text-neutral-400 font-mono">
          <div>
            <span>V: {{ (emoState?.valence ?? 0.5).toFixed(2) }}</span>
            <span class="ml-2">A: {{ (emoState?.arousal ?? 0.5).toFixed(2) }}</span>
          </div>
          <div v-if="emoState?.is_jealous" class="flex items-center gap-1 text-rose-400 font-bold animate-pulse">
            <div class="i-solar:danger-triangle-bold" />
            <span>吃醋中!</span>
          </div>
          <div v-else class="text-neutral-500">
            状态正常
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>
