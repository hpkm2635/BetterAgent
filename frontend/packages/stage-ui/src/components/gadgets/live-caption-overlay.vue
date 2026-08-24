<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { computed, ref, watch } from 'vue'
import { useSpeakingStore } from '../../stores/audio'
import { useChatStreamStore } from '../../stores/chat/stream-store'
import { useBetterAgentGatewayStore } from '../../stores/modules/betteragent-gateway'
import { useSpeechOutputControlStore } from '../../stores/speech-output-control'

const streamStore = useChatStreamStore()
const gatewayStore = useBetterAgentGatewayStore()
const { isSpeaking, csmState, isStreaming, isGracePeriodActive, revealedCaption, citations } = storeToRefs(gatewayStore)
const { speechMuted } = storeToRefs(useSpeechOutputControlStore())
// Stage.vue keeps this true for as long as it still has BetterAgent audio
// queued/playing on audioContext's own clock (see betterAgentSilenceTimer),
// which is a more trustworthy "still talking" signal than the server's
// agent.state_change broadcast alone -- the server only knows when it's
// done *producing* audio, not when the browser is done *playing* it, and
// production can outrun realtime playback for long multi-sentence replies.
const { nowSpeaking } = storeToRefs(useSpeakingStore())

// Normally the typewriter-paced revealedCaption (Stage.vue reveals it chunk
// by chunk as each chunk's audio actually starts playing) is the source of
// truth. When muted, Stage.vue's audio-chunk-scheduling path never runs
// (playBetterAgentAudioChunk returns immediately), so no reveal data ever
// arrives -- fall back to showing the full accumulated text immediately, so
// captions still work as a silent-mode substitute for audio. Gating on
// speechMuted (a stable flag) rather than "is revealedCaption empty" avoids
// a flicker: revealedCaption is legitimately empty for a brief moment at
// the start of every turn, before the first chunk's reveal timer fires.
const displayText = computed(() => {
  if (!speechMuted.value)
    return revealedCaption.value

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

// Subtitle visibility is bound to CSM TALKING state, audio playback, active streaming, inter-turn grace period, and
// Stage.vue's own real playback-queue-completion signal (nowSpeaking) -- see its import above for why the latter matters.
const isVisible = computed(() => {
  const isTalking = nowSpeaking.value || isSpeaking.value || csmState.value === 'talking' || isStreaming.value || isGracePeriodActive.value
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

// Collapsed by default -- citations are a supplementary "how do you know
// that" detail, not something that should compete with the caption text
// for attention. Not reset on its own; citations.value itself clears at
// the start of each new turn (betteragent-gateway.ts), which naturally
// hides the whole section again regardless of this toggle's state.
const citationsExpanded = ref(false)
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

        <!-- Campus KB Citations (collapsed by default) -->
        <div v-if="citations.length" class="border-t border-neutral-200/50 px-5 py-2 dark:border-white/10">
          <button
            type="button"
            class="flex items-center gap-1 text-[11px] font-medium text-neutral-400 transition-colors hover:text-pink-500 dark:text-neutral-500"
            @click.stop="citationsExpanded = !citationsExpanded"
          >
            参考资料 ({{ citations.length }})
            <div :class="citationsExpanded ? 'i-carbon-chevron-up' : 'i-carbon-chevron-down'" />
          </button>
          <ul v-if="citationsExpanded" class="mt-2 flex flex-col gap-2">
            <li
              v-for="(c, i) in citations"
              :key="i"
              class="rounded-lg bg-neutral-100/70 p-2 text-xs text-neutral-600 dark:bg-neutral-800/60 dark:text-neutral-300"
            >
              <div class="mb-1 text-[10px] font-medium uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
                {{ c.source }}
              </div>
              <div class="line-clamp-3 leading-relaxed">
                {{ c.content }}
              </div>
            </li>
          </ul>
        </div>
      </div>
    </div>
  </Transition>
</template>
