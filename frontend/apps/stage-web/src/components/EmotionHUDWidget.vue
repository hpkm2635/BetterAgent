<script setup lang="ts">
import { useBetterAgentGatewayStore } from '@proj-airi/stage-ui/stores/modules/betteragent-gateway'
import { breakpointsTailwind, useBreakpoints, useResizeObserver, useScreenSafeArea } from '@vueuse/core'
import { DialogContent, DialogOverlay, DialogPortal, DialogRoot } from 'reka-ui'
import { DrawerContent, DrawerHandle, DrawerOverlay, DrawerPortal, DrawerRoot } from 'vaul-vue'
import { computed, onMounted } from 'vue'

const gatewayStore = useBetterAgentGatewayStore()
const showDialog = defineModel<boolean>({ default: false })

const breakpoints = useBreakpoints(breakpointsTailwind)
const isDesktop = breakpoints.greaterOrEqual('md')
const screenSafeArea = useScreenSafeArea()

useResizeObserver(document.documentElement, () => screenSafeArea.update())
onMounted(() => screenSafeArea.update())

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
  <div>
    <!-- Desktop Modal Dialog (reka-ui) -->
    <DialogRoot v-if="isDesktop" :open="showDialog" @update:open="value => showDialog = value">
      <DialogPortal>
        <DialogOverlay class="fixed inset-0 z-[9999] bg-black/50 backdrop-blur-sm data-[state=closed]:animate-fadeOut data-[state=open]:animate-fadeIn" />
        <DialogContent
          class="fixed left-1/2 top-1/2 z-[9999] w-[92dvw] max-w-md transform overflow-hidden rounded-2xl border border-neutral-700/60 bg-neutral-950/90 p-5 shadow-2xl backdrop-blur-xl -translate-x-1/2 -translate-y-1/2 data-[state=closed]:animate-contentHide data-[state=open]:animate-contentShow text-neutral-200 flex flex-col gap-4"
        >
          <!-- Header & Mood Badge -->
          <div class="flex items-center justify-between border-b border-neutral-800 pb-2.5">
            <div class="flex items-center gap-2 font-semibold text-base">
              <div class="i-solar:cat-bold text-primary-400 text-lg" />
              <span>Camelia 情绪指标</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="rounded-full border px-2.5 py-0.5 font-mono text-xs font-bold" :class="moodColorClass">
                {{ emoState?.mood || 'NEUTRAL' }}
              </span>
              <button class="text-neutral-400 hover:text-rose-400 p-1" title="关闭" @click="showDialog = false">
                <div class="i-solar:close-circle-bold text-base" />
              </button>
            </div>
          </div>

          <!-- Affection Bar -->
          <div class="flex flex-col gap-1.5">
            <div class="flex justify-between text-neutral-300 text-xs">
              <span class="flex items-center gap-1.5">
                <div class="i-solar:heart-bold text-rose-500 text-sm" />
                好感度 (Affection)
              </span>
              <span class="font-mono text-rose-400 font-bold">{{ affectionPercent.toFixed(1) }} / 100</span>
            </div>
            <div class="h-2.5 w-full overflow-hidden rounded-full bg-neutral-800/80">
              <div
                class="h-full rounded-full bg-gradient-to-r from-rose-500 to-pink-400 transition-all duration-500"
                :style="{ width: `${affectionPercent}%` }"
              />
            </div>
          </div>

          <!-- Energy & Social Battery -->
          <div class="grid grid-cols-2 gap-4">
            <!-- Energy -->
            <div class="flex flex-col gap-1.5">
              <div class="flex justify-between text-neutral-400 text-xs">
                <span class="flex items-center gap-1">
                  <div class="i-solar:bolt-bold text-amber-400" />
                  精力
                </span>
                <span class="font-mono text-amber-400 font-semibold">{{ energyPercent.toFixed(0) }}%</span>
              </div>
              <div class="h-2 w-full overflow-hidden rounded-full bg-neutral-800">
                <div
                  class="h-full rounded-full bg-amber-400 transition-all duration-500"
                  :style="{ width: `${energyPercent}%` }"
                />
              </div>
            </div>

            <!-- Social Battery -->
            <div class="flex flex-col gap-1.5">
              <div class="flex justify-between text-neutral-400 text-xs">
                <span class="flex items-center gap-1">
                  <div class="i-solar:battery-charge-bold text-emerald-400" />
                  社交电量
                </span>
                <span class="font-mono text-emerald-400 font-semibold">{{ batteryPercent.toFixed(0) }}%</span>
              </div>
              <div class="h-2 w-full overflow-hidden rounded-full bg-neutral-800">
                <div
                  class="h-full rounded-full bg-emerald-400 transition-all duration-500"
                  :style="{ width: `${batteryPercent}%` }"
                />
              </div>
            </div>
          </div>

          <!-- VAD Coordinates & Jealous Status -->
          <div class="flex items-center justify-between border-t border-neutral-800 pt-3 text-xs text-neutral-400 font-mono">
            <div>
              <span>V: {{ (emoState?.valence ?? 0.5).toFixed(2) }}</span>
              <span class="ml-3">A: {{ (emoState?.arousal ?? 0.5).toFixed(2) }}</span>
            </div>
            <div v-if="emoState?.is_jealous" class="flex items-center gap-1 text-rose-400 font-bold animate-pulse">
              <div class="i-solar:danger-triangle-bold" />
              <span>吃醋中!</span>
            </div>
            <div v-else class="text-neutral-500">
              状态正常
            </div>
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

          <!-- Header & Mood Badge -->
          <div class="flex items-center justify-between border-b border-neutral-800 pb-3 mb-3">
            <div class="flex items-center gap-2 font-semibold text-base">
              <div class="i-solar:cat-bold text-primary-400 text-lg" />
              <span>Camelia 情绪指标</span>
            </div>
            <span class="rounded-full border px-2.5 py-0.5 font-mono text-xs font-bold" :class="moodColorClass">
              {{ emoState?.mood || 'NEUTRAL' }}
            </span>
          </div>

          <div class="flex flex-col gap-4 overflow-y-auto">
            <!-- Affection Bar -->
            <div class="flex flex-col gap-1.5">
              <div class="flex justify-between text-neutral-300 text-xs">
                <span class="flex items-center gap-1.5">
                  <div class="i-solar:heart-bold text-rose-500 text-sm" />
                  好感度 (Affection)
                </span>
                <span class="font-mono text-rose-400 font-bold">{{ affectionPercent.toFixed(1) }} / 100</span>
              </div>
              <div class="h-2.5 w-full overflow-hidden rounded-full bg-neutral-800">
                <div
                  class="h-full rounded-full bg-gradient-to-r from-rose-500 to-pink-400 transition-all duration-500"
                  :style="{ width: `${affectionPercent}%` }"
                />
              </div>
            </div>

            <!-- Energy & Social Battery -->
            <div class="grid grid-cols-2 gap-4">
              <div class="flex flex-col gap-1.5">
                <div class="flex justify-between text-neutral-400 text-xs">
                  <span class="flex items-center gap-1">
                    <div class="i-solar:bolt-bold text-amber-400" />
                    精力
                  </span>
                  <span class="font-mono text-amber-400 font-semibold">{{ energyPercent.toFixed(0) }}%</span>
                </div>
                <div class="h-2 w-full overflow-hidden rounded-full bg-neutral-800">
                  <div
                    class="h-full rounded-full bg-amber-400 transition-all duration-500"
                    :style="{ width: `${energyPercent}%` }"
                  />
                </div>
              </div>

              <div class="flex flex-col gap-1.5">
                <div class="flex justify-between text-neutral-400 text-xs">
                  <span class="flex items-center gap-1">
                    <div class="i-solar:battery-charge-bold text-emerald-400" />
                    社交电量
                  </span>
                  <span class="font-mono text-emerald-400 font-semibold">{{ batteryPercent.toFixed(0) }}%</span>
                </div>
                <div class="h-2 w-full overflow-hidden rounded-full bg-neutral-800">
                  <div
                    class="h-full rounded-full bg-emerald-400 transition-all duration-500"
                    :style="{ width: `${batteryPercent}%` }"
                  />
                </div>
              </div>
            </div>

            <div class="flex items-center justify-between border-t border-neutral-800 pt-3 text-xs text-neutral-400 font-mono">
              <div>
                <span>V: {{ (emoState?.valence ?? 0.5).toFixed(2) }}</span>
                <span class="ml-3">A: {{ (emoState?.arousal ?? 0.5).toFixed(2) }}</span>
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
        </DrawerContent>
      </DrawerPortal>
    </DrawerRoot>
  </div>
</template>
