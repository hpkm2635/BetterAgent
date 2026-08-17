import type { StreamingAssistantMessage } from '../../types/chat'

import { cloneDeep } from 'es-toolkit'
import { nanoid } from 'nanoid'
import { defineStore } from 'pinia'
import { ref, toRaw } from 'vue'

import { useChatSessionStore } from './session-store'

export const useChatStreamStore = defineStore('chat-stream', () => {
  const chatSession = useChatSessionStore()
  const streamingMessage = ref<StreamingAssistantMessage>({ id: nanoid(), role: 'assistant', content: '', slices: [], tool_results: [], createdAt: Date.now() })

  function beginStream() {
    streamingMessage.value = { id: nanoid(), role: 'assistant', content: '', slices: [], tool_results: [], createdAt: Date.now() }
  }

  function appendStreamLiteral(literal: string) {
    streamingMessage.value.content += literal

    const lastSlice = streamingMessage.value.slices.at(-1)
    if (lastSlice?.type === 'text') {
      lastSlice.text += literal
      return
    }

    streamingMessage.value.slices.push({
      type: 'text',
      text: literal,
    })
  }

  function finalizeStream(fullText?: string) {
    const sessionId = chatSession.activeSessionId
    if (streamingMessage.value.slices.length > 0 || streamingMessage.value.content) {
      const msg = cloneDeep(toRaw(streamingMessage.value))
      if (!msg.id)
        msg.id = nanoid()
      chatSession.appendSessionMessage(sessionId, msg)
    }
    streamingMessage.value = { id: nanoid(), role: 'assistant', content: '', slices: [], tool_results: [] }
    if (fullText)
      streamingMessage.value.content = fullText
  }

  function resetStream() {
    streamingMessage.value = { role: 'assistant', content: '', slices: [], tool_results: [] }
  }

  return {
    streamingMessage,
    beginStream,
    appendStreamLiteral,
    finalizeStream,
    resetStream,
  }
})
