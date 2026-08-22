<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { computed, ref, watch } from 'vue'
import { useChatStreamStore } from '../../stores/chat/stream-store'
import { useBetterAgentGatewayStore } from '../../stores/modules/betteragent-gateway'

const streamStore = useChatStreamStore()
const gatewayStore = useBetterAgentGatewayStore()
const { isSpeaking, csmState, isStreaming, isGracePeriodActive } = storeToRefs(gatewayStore)

const displayText = computed(() => {
  const rawContent = streamStore.streamingMessage?.content
  if (typeof rawContent === 'string' && rawContent.trim()) {
    return rawContent.trim()
  }
  if (Array.isArray(rawContent)) {
    const text = rawContent
      .map(part => (typeof part === 'string' ? part : 'text' in part ? part.text : ''))
      .join('')
      .trim()
    if (text)
      return text
  }
  const slicesText = (streamStore.streamingMessage?.slices || [])
    .filter(s => s.type === 'text')
    .map(s => s.text)
    .join('')
  return slicesText.trim()
})

const lastNonEmptyText = ref('')

watch(displayText, (newVal) => {
  if (newVal) {
    lastNonEmptyText.value = newVal
  }
})

// Subtitle visibility is bound to CSM TALKING state, audio playback, active streaming, and inter-turn grace period
const isVisible = computed(() => {
  const isTalking = isSpeaking.value || csmState.value === 'talking' || isStreaming.value || isGracePeriodActive.value
  return isTalking && !!lastNonEmptyText.value
})

// Draggable Position State & Pointer Event Handlers
const el = ref<HTMLElement>()
const dragOffset = ref<{ x: number, y: number } | null>(null)

let isDragging = false
let startPointerY = 0
let startPointerX = 0
let startOffsetY = 0
let startOffsetX = 0

function onPointerDown(e: PointerEvent) {
  if (!el.value)
    return
  isDragging = true
  startPointerY = e.clientY
  startPointerX = e.clientX
  startOffsetY = dragOffset.value ? dragOffset.value.y : 0
  startOffsetX = dragOffset.value ? dragOffset.value.x : 0

  const target = e.currentTarget as HTMLElement | null
  target?.setPointerCapture?.(e.pointerId)
}

function onPointerMove(e: PointerEvent) {
  if (!isDragging)
    return
  const deltaY = e.clientY - startPointerY
  const deltaX = e.clientX - startPointerX
  dragOffset.value = {
    x: startOffsetX + deltaX,
    y: startOffsetY + deltaY,
  }
}

function onPointerUp(e: PointerEvent) {
  if (isDragging) {
    isDragging = false
    const target = e.currentTarget as HTMLElement | null
    target?.releasePointerCapture?.(e.pointerId)
  }
}

function resetPosition() {
  dragOffset.value = null
}
</script>

<template>
  <Transition
    enter-active-class="transition-all duration-300 ease-out"
    enter-from-class="opacity-0 translate-y-3 scale-95"
    enter-to-class="opacity-100 translate-y-0 scale-100"
    leave-active-class="transition-all duration-500 ease-in"
    leave-from-class="opacity-100 translate-y-0 scale-100"
    leave-to-class="opacity-0 translate-y-2 scale-95"
  >
    <div
      v-if="isVisible"
      ref="el"
      class="pointer-events-auto absolute bottom-32 left-1/2 z-30 max-w-[92%] min-w-[280px] select-none md:bottom-28 md:max-w-[640px]"
      :style="{
        transform: `translate(calc(-50% + ${dragOffset?.x ?? 0}px), ${dragOffset?.y ?? 0}px)`,
        paddingBottom: 'env(safe-area-inset-bottom, 0px)',
      }"
    >
      <div
        class="group relative flex flex-col border border-neutral-200/50 rounded-2xl bg-white/90 shadow-2xl backdrop-blur-xl transition-shadow duration-200 cursor-grab active:cursor-grabbing dark:border-white/15 dark:bg-neutral-900/90"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
      >
        <!-- Top Drag Bar / Gripper -->
        <div class="flex items-center justify-between px-4 pb-1 pt-2 text-xs text-neutral-400 dark:text-neutral-500">
          <div class="flex items-center gap-2">
            <!-- Cute Live Indicator -->
            <span class="relative flex h-2.5 w-2.5 flex-shrink-0">
              <span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-pink-400 opacity-75" />
              <span class="relative inline-flex h-2.5 w-2.5 rounded-full bg-pink-500" />
            </span>
            <span class="text-[11px] font-medium tracking-wider text-neutral-500 uppercase dark:text-neutral-400 opacity-80">实时字幕 (按住拖动)</span>
          </div>

          <!-- Drag Handle Icon & Reset Button -->
          <div class="flex items-center gap-2 opacity-60 transition-opacity group-hover:opacity-100">
            <button
              v-if="dragOffset"
              type="button"
              class="rounded bg-neutral-200/80 px-1.5 py-0.5 text-[10px] text-neutral-700 transition-colors hover:bg-pink-500 hover:text-white dark:bg-neutral-800 dark:text-neutral-300"
              title="重置位置"
              @click.stop="resetPosition"
            >
              重置位置
            </button>
            <div class="i-carbon-draggable cursor-grab text-sm active:cursor-grabbing" />
          </div>
        </div>

        <!-- Subtitle Content -->
        <div class="break-words px-5 pb-3.5 pt-1 text-sm font-medium leading-relaxed tracking-wide text-neutral-800 md:text-base dark:text-neutral-100">
          {{ lastNonEmptyText }}
        </div>
      </div>
    </div>
  </Transition>
</template>
