<script setup lang="ts">
import type { ChatHistoryItem } from '../../../../types/chat'

import { isStageCapacitor, isStageWeb } from '@proj-airi/stage-shared'
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { MarkdownRenderer } from '../../../markdown'
import { ChatActionMenu } from '../components/action-menu'
import { getChatHistoryItemCopyText } from '../utils'

const props = withDefaults(defineProps<{
  message: ChatHistoryItem
  label: string
  variant?: 'desktop' | 'mobile'
}>(), {
  variant: 'desktop',
})

const emit = defineEmits<{
  (e: 'copy'): void
  (e: 'delete'): void
}>()

const content = computed(() => {
  const raw = props.message.content
  if (typeof raw === 'string')
    return raw

  if (Array.isArray(raw)) {
    const textPart = raw.find(part => 'type' in part && part.type === 'text') as { text?: string } | undefined
    if (textPart?.text)
      return textPart.text

    return raw.map(entry => JSON.stringify(entry)).join('\n')
  }

  return ''
})

const isVoice = computed(() => !!props.message.voice)
const voiceTranscript = computed(() => props.message.voice?.transcript || content.value)
const copyText = computed(() => getChatHistoryItemCopyText(props.message as ChatHistoryItem))

const showTranscript = ref(false)
const transcriptPos = ref({ x: 0, y: 0 })

function onContextMenu(event: MouseEvent) {
  if (!isVoice.value)
    return
  event.preventDefault()
  transcriptPos.value = { x: event.clientX, y: event.clientY }
  showTranscript.value = true
}

function hideTranscript() {
  showTranscript.value = false
}

onMounted(() => {
  window.addEventListener('click', hideTranscript, { capture: true })
})

onUnmounted(() => {
  window.removeEventListener('click', hideTranscript, { capture: true })
})

const containerClasses = computed(() => [
  'flex',
  props.variant === 'mobile' ? 'ml-0 flex-row' : 'ml-12 flex-row-reverse',
])

const boxClasses = computed(() => [
  props.variant === 'mobile' ? 'px-2 py-2 text-sm bg-neutral-100/90 dark:bg-neutral-800/90' : 'px-3 py-3 bg-neutral-100/80 dark:bg-neutral-800/80',
])
</script>

<template>
  <div v-if="message.role === 'user'" :class="containerClasses" class="ph-no-capture">
    <ChatActionMenu
      :copy-text="copyText"
      placement="left"
      @copy="emit('copy')"
      @delete="emit('delete')"
    >
      <template #default="{ setMeasuredElement }">
        <div
          :ref="setMeasuredElement"
          flex="~ col" shadow="sm neutral-200/50 dark:none"
          min-w-20 rounded-xl h="unset <sm:fit"
          :class="[
            boxClasses,
            (isStageWeb() || isStageCapacitor()) && props.variant === 'mobile' ? 'select-none sm:select-auto' : '',
          ]"
        >
          <div>
            <span text-sm text="black/60 dark:white/65" font-normal class="inline <sm:hidden">{{ label }}</span>
          </div>

          <div
            v-if="isVoice"
            class="voice-bubble flex items-center gap-2 cursor-pointer"
            @contextmenu="onContextMenu"
          >
            <div class="voice-icon flex h-8 w-8 items-center justify-center rounded-full bg-primary-500/10 text-primary-600 dark:text-primary-300">
              <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="23" />
                <line x1="8" y1="23" x2="16" y2="23" />
              </svg>
            </div>
            <span class="text-sm text-black/70 dark:text-white/70">语音输入</span>
            <span class="text-xs text-black/40 dark:text-white/40">右键查看转写</span>
          </div>

          <MarkdownRenderer
            v-else
            :content="content as string"
            class="break-words"
          />
        </div>
      </template>
    </ChatActionMenu>

    <div
      v-if="showTranscript"
      class="fixed z-50 max-w-xs rounded-lg border border-neutral-200 bg-white p-3 shadow-lg dark:border-neutral-700 dark:bg-neutral-900"
      :style="{ left: (transcriptPos.x + 'px'), top: (transcriptPos.y + 'px') }"
      @click.stop
    >
      <div class="mb-1 text-xs text-neutral-500 dark:text-neutral-400">转写文字</div>
      <div class="text-sm text-black dark:text-white">{{ voiceTranscript }}</div>
    </div>
  </div>
</template>
