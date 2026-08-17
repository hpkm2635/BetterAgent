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
      class="pointer-events-none absolute bottom-24 left-1/2 z-30 max-w-[92%] min-w-[260px] -translate-x-1/2 transform select-none md:bottom-20 md:max-w-[640px]"
      style="padding-bottom: env(safe-area-inset-bottom, 0px);"
    >
      <div
        class="relative flex items-center gap-3 border border-neutral-200/40 dark:border-white/10 rounded-2xl bg-white/80 dark:bg-neutral-900/85 px-5 py-3.5 shadow-xl backdrop-blur-xl transition-all duration-300"
      >
        <!-- Cute Live Indicator -->
        <span class="relative flex h-2.5 w-2.5 flex-shrink-0">
          <span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-pink-400 opacity-75" />
          <span class="relative inline-flex h-2.5 w-2.5 rounded-full bg-pink-500" />
        </span>

        <!-- Subtitle Content -->
        <div class="flex-1 break-words text-sm font-medium leading-relaxed tracking-wide text-neutral-800 dark:text-neutral-100 md:text-base">
          {{ lastNonEmptyText }}
        </div>
      </div>
    </div>
  </Transition>
</template>
