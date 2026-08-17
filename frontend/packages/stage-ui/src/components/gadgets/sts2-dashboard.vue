<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { computed } from 'vue'
import { useSTS2GameStateStore } from '../../stores/modules/sts2-game-state'

const gameStateStore = useSTS2GameStateStore()
const { floor, hp, maxHp, gold, act, isLikelyActive } = storeToRefs(gameStateStore)

const hpPercent = computed(() => {
  if (!maxHp.value || maxHp.value <= 0)
    return 100
  return Math.max(0, Math.min(100, Math.round((hp.value / maxHp.value) * 100)))
})

const hpBarColor = computed(() => {
  const p = hpPercent.value
  if (p > 50)
    return 'bg-gradient-to-r from-emerald-500 to-green-400'
  if (p > 25)
    return 'bg-gradient-to-r from-amber-500 to-yellow-400'
  return 'bg-gradient-to-r from-rose-600 to-red-500 animate-pulse'
})
</script>

<template>
  <Transition
    enter-active-class="transition-all duration-400 ease-out"
    enter-from-class="opacity-0 -translate-y-4 scale-95"
    enter-to-class="opacity-100 translate-y-0 scale-100"
    leave-active-class="transition-all duration-300 ease-in"
    leave-from-class="opacity-100 translate-y-0 scale-100"
    leave-to-class="opacity-0 -translate-y-3 scale-95"
  >
    <div
      v-if="isLikelyActive"
      class="pointer-events-none absolute right-4 top-4 z-25 min-w-[240px] select-none"
    >
      <div
        class="border border-neutral-200/40 dark:border-white/10 rounded-2xl bg-white/80 dark:bg-neutral-900/85 p-3.5 shadow-xl backdrop-blur-xl transition-all duration-300"
      >
        <!-- Header -->
        <div class="mb-2.5 flex items-center justify-between">
          <div class="flex items-center gap-1.5 font-bold tracking-wider text-xs text-amber-500 dark:text-amber-400 uppercase">
            <span>⚔️ Slay The Spire 2</span>
          </div>
          <div class="flex items-center gap-1.5 rounded-full bg-red-500/20 px-2 py-0.5 font-semibold text-[10px] text-red-400">
            <span class="h-1.5 w-1.5 animate-ping rounded-full bg-red-400" />
            LIVE
          </div>
        </div>

        <!-- Badges Row -->
        <div class="mb-2.5 flex items-center gap-2 text-xs">
          <div class="flex items-center gap-1 border border-neutral-200/60 dark:border-white/10 rounded-lg bg-neutral-100/80 dark:bg-white/10 px-2 py-1 font-medium text-neutral-800 dark:text-white/90">
            <span class="text-neutral-500 dark:text-white/60">Act</span>
            <span class="font-bold text-sky-600 dark:text-sky-400">{{ act }}</span>
          </div>
          <div class="flex items-center gap-1 border border-neutral-200/60 dark:border-white/10 rounded-lg bg-neutral-100/80 dark:bg-white/10 px-2 py-1 font-medium text-neutral-800 dark:text-white/90">
            <span class="text-neutral-500 dark:text-white/60">Floor</span>
            <span class="font-bold text-indigo-600 dark:text-indigo-300">{{ floor }}</span>
          </div>
          <div class="flex items-center gap-1 border border-neutral-200/60 dark:border-white/10 rounded-lg bg-neutral-100/80 dark:bg-white/10 px-2 py-1 font-medium text-neutral-800 dark:text-white/90">
            <span>🪙</span>
            <span class="font-bold text-amber-600 dark:text-amber-300">{{ gold }}</span>
          </div>
        </div>

        <!-- HP Bar -->
        <div class="space-y-1">
          <div class="flex items-center justify-between text-xs font-semibold">
            <span class="flex items-center gap-1 text-rose-500 dark:text-rose-400">
              <span>❤️</span> HP
            </span>
            <span class="text-neutral-800 dark:text-white/90">
              {{ hp }} <span class="text-neutral-400 dark:text-white/50">/ {{ maxHp }}</span>
            </span>
          </div>
          <div class="h-2 w-full overflow-hidden rounded-full bg-neutral-200/60 dark:bg-white/15">
            <div
              class="h-full rounded-full transition-all duration-500 ease-out"
              :class="hpBarColor"
              :style="{ width: `${hpPercent}%` }"
            />
          </div>
        </div>
      </div>
    </div>
  </Transition>
</template>
