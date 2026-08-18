import { defineStore } from 'pinia'
import { ref } from 'vue'
import { betterAgentWSBridge } from '../../services/betteragent-ws'
import { useChatStreamStore } from '../chat/stream-store'
import { useSTS2GameStateStore } from './sts2-game-state'

import type { EmotionalStatePayload } from '../../services/betteragent-ws'

export const useBetterAgentGatewayStore = defineStore('betteragent-gateway', () => {
  const currentChatId = ref<number | null>(null)
  const csmState = ref<'idle' | 'talking' | 'thinking' | 'listening' | 'executing_action' | string>('idle')
  const isSpeaking = ref(false)
  const isStreaming = ref(false)
  const isGracePeriodActive = ref(false)
  const lastEmotion = ref('')
  const lastAction = ref('')
  const emotionalState = ref<EmotionalStatePayload | null>(null)

  const streamStore = useChatStreamStore()
  const sts2GameState = useSTS2GameStateStore()

  let streamingWatchdog: ReturnType<typeof setTimeout> | null = null
  let graceTimer: ReturnType<typeof setTimeout> | null = null

  function resetStreamingWatchdog() {
    if (streamingWatchdog) {
      clearTimeout(streamingWatchdog)
      streamingWatchdog = null
    }
  }

  function touchStreamingWatchdog() {
    resetStreamingWatchdog()
    streamingWatchdog = setTimeout(() => {
      if (isStreaming.value) {
        console.warn('[BetterAgentGateway] Stream watchdog timeout reached, resetting isStreaming state')
        isStreaming.value = false
        isGracePeriodActive.value = false
        streamStore.finalizeStream()
      }
    }, 45000)
  }

  function triggerGracePeriod() {
    isGracePeriodActive.value = true
    if (graceTimer)
      clearTimeout(graceTimer)
    // 1200ms grace period bridges the gap between LLM isFinal=true and first TTS audio chunk arrival
    graceTimer = setTimeout(() => {
      isGracePeriodActive.value = false
      graceTimer = null
    }, 1200)
  }

  function getResolvedChatId(): number | null {
    if (currentChatId.value)
      return currentChatId.value

    if (typeof window !== 'undefined') {
      const urlParams = new URLSearchParams(window.location.search)
      const queryChatId = urlParams.get('chat_id')
      if (queryChatId && !Number.isNaN(Number(queryChatId))) {
        currentChatId.value = Number(queryChatId)
        return currentChatId.value
      }
    }
    return null
  }

  function isChatMatch(msgChatId?: number | null): boolean {
    if (!msgChatId)
      return true
    const active = getResolvedChatId()
    if (!active)
      return true
    return active === msgChatId
  }

  let unsubs: Array<() => void> = []

  let initialized = false
  function initialize(pinnedChatId?: number) {
    if (pinnedChatId) {
      currentChatId.value = pinnedChatId
    }

    if (initialized)
      return
    initialized = true

    // Clean up previous listeners if re-initializing
    unsubs.forEach(unsub => unsub())
    unsubs = []

    betterAgentWSBridge.connect()

    // 1. Text Delta & Stream Life Cycle
    unsubs.push(betterAgentWSBridge.onTextDelta((text: string, isFinal?: boolean, chatId?: number) => {
      if (!isChatMatch(chatId))
        return

      touchStreamingWatchdog()
      if (!isStreaming.value) {
        isStreaming.value = true
        streamStore.beginStream()
      }
      streamStore.appendStreamLiteral(text)

      if (isFinal) {
        resetStreamingWatchdog()
        isStreaming.value = false
        triggerGracePeriod()
        streamStore.finalizeStream()
      }
    }))

    // 2. CSM State Transitions
    unsubs.push(betterAgentWSBridge.onStateChange((state: string, chatId?: number) => {
      if (!isChatMatch(chatId))
        return

      const normalized = state.toLowerCase()
      csmState.value = normalized
      if (normalized === 'talking') {
        isSpeaking.value = true
      }
      else if (normalized === 'idle') {
        isSpeaking.value = false
        isStreaming.value = false
        isGracePeriodActive.value = false
        if (graceTimer) {
          clearTimeout(graceTimer)
          graceTimer = null
        }
        resetStreamingWatchdog()
      }
    }))

    // 3. Audio Chunks
    unsubs.push(betterAgentWSBridge.onAudioChunk(() => {
      isSpeaking.value = true
    }))

    // 4. Emotion & Action Tokens
    unsubs.push(betterAgentWSBridge.onEmotion((emotion: string, action?: string) => {
      lastEmotion.value = emotion
      if (action)
        lastAction.value = action
    }))

    unsubs.push(betterAgentWSBridge.onEmotionState((state) => {
      emotionalState.value = state
    }))

    // 5. Game State
    unsubs.push(betterAgentWSBridge.onGameState((state) => {
      sts2GameState.updateState(state)
    }))
  }

  return {
    currentChatId,
    csmState,
    isSpeaking,
    isStreaming,
    isGracePeriodActive,
    lastEmotion,
    lastAction,
    emotionalState,
    initialize,
  }
})
