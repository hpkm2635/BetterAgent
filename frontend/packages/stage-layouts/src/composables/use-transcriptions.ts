import type { MaybeRefOrGetter, Ref } from 'vue'

import { betterAgentWSBridge } from '@proj-airi/stage-ui/services/betteragent-ws'
import { useHearingSpeechInputPipeline, useHearingStore } from '@proj-airi/stage-ui/stores/modules/hearing'
import { useProvidersStore } from '@proj-airi/stage-ui/stores/providers'
import { useSettingsAudioDevice } from '@proj-airi/stage-ui/stores/settings'
import { until } from '@vueuse/core'
import { storeToRefs } from 'pinia'
import { nextTick, onScopeDispose, ref, toValue, watch } from 'vue'

interface TranscriptionOptions {
  messageInputRef: Ref<string>
  sendMessage: () => void
  isStageTamagotchi: MaybeRefOrGetter<boolean>
}

export function useTranscriptions(options: TranscriptionOptions) {
  const { messageInputRef: messageInput, sendMessage, isStageTamagotchi } = options

  const hearingStore = useHearingStore()
  const audioDeviceSettingsStore = useSettingsAudioDevice()
  const hearingPipeline = useHearingSpeechInputPipeline()
  const { transcribeForMediaStream, stopStreamingTranscription } = hearingPipeline
  const { supportsStreamInput } = storeToRefs(hearingPipeline)
  const { configured: hearingConfigured, autoSendEnabled, autoSendDelay } = storeToRefs(hearingStore)
  const { enabled: hearingEnabled, stream } = storeToRefs(audioDeviceSettingsStore)
  const providersStore = useProvidersStore()
  const { askPermission, startStream } = audioDeviceSettingsStore

  const isListening = ref(false)
  // isListening only flips to true after transcribeForMediaStream resolves
  // (which includes an `await audioContext.resume()` gap for the BetterAgent
  // iFLYTEK STT path) -- a second startStreaming() call landing in that
  // window would still see isListening === false and race in, stacking a
  // second AudioContext/ScriptProcessorNode on the same shared mic stream.
  // The loser is never cleaned up (streamingSession only tracks the winner),
  // so it keeps firing onaudioprocess forever. This flag closes that gap.
  let startInFlight = false

  // Auto-send logic
  let autoSendTimeout: ReturnType<typeof setTimeout> | undefined
  function clearPendingAutoSend() {
    if (autoSendTimeout) {
      clearTimeout(autoSendTimeout)
      autoSendTimeout = undefined
    }
  }
  async function debouncedAutoSend() {
    // Double-check auto-send is enabled before proceeding
    if (!autoSendEnabled.value) {
      clearPendingAutoSend()
      return
    }
    if (autoSendTimeout) {
      clearTimeout(autoSendTimeout)
    }

    autoSendTimeout = setTimeout(async () => {
      // Final check before sending - auto-send might have been disabled while waiting
      if (!autoSendEnabled.value) {
        clearPendingAutoSend()
        return
      }
      sendMessage()
      autoSendTimeout = undefined
    }, autoSendDelay.value)
  }

  const stopStreaming = async () => {
    if (!isListening.value)
      return

    try {
      console.info('Stopping transcription...', { source: 'useTranscriptions' })
      clearPendingAutoSend()
      await stopStreamingTranscription(true)
      isListening.value = false
      console.info('Transcription stopped', { source: 'useTranscriptions' })
    }
    catch (err) {
      console.error('Error stopping transcription:', err, { source: 'useTranscriptions' })
      isListening.value = false
    }
  }

  const startStreaming = async () => {
    if (startInFlight || isListening.value) {
      console.info('Start already in progress or already listening, ignoring duplicate call', { source: 'useTranscriptions' })
      return
    }
    startInFlight = true

    try {
      await startStreamingInner()
    }
    finally {
      startInFlight = false
    }
  }

  const startStreamingInner = async () => {
    console.info('Starting streaming transcription', {
      enabled: hearingEnabled.value,
      hasStream: !!stream.value,
      supportsStreamInput: supportsStreamInput.value,
      hearingConfigured: hearingConfigured.value,
    }, { source: 'useTranscriptions' })

    // Auto-configure BetterAgent iFLYTEK STT as default if no provider is configured.
    // Falls back to Web Speech API when the backend bridge is not connected.
    if (!hearingConfigured.value) {
      console.info('No transcription provider configured. Auto-configuring BetterAgent iFLYTEK STT as default', { source: 'useTranscriptions' })

      const isWebSpeechAvailable = typeof window !== 'undefined'
        && !toValue(isStageTamagotchi) // Explicitly exclude Electron
        && ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window)

      if (!isWebSpeechAvailable) {
        const errorMsg = 'Web Speech API is not available and no transcription provider is configured. Please go to Settings > Modules > Hearing to configure a transcription provider.'
        console.error(errorMsg, 'Browser support:', {
          hasWindow: typeof window !== 'undefined',
          hasWebkitSpeechRecognition: typeof window !== 'undefined' && 'webkitSpeechRecognition' in window,
          hasSpeechRecognition: typeof window !== 'undefined' && 'SpeechRecognition' in window,
        }, { source: 'useTranscriptions' })
        isListening.value = false
        return
      }

      providersStore.initializeProvider('betteragent-iflytek-stt')
      providersStore.initializeProvider('browser-web-speech-api')
      await nextTick()

      // Prefer backend iFLYTEK STT when the WebSocket bridge is connected;
      // otherwise fall back to the browser-native Web Speech API.
      if (betterAgentWSBridge.isConnected()) {
        hearingStore.activeTranscriptionProvider = 'betteragent-iflytek-stt'
        console.info('BetterAgent iFLYTEK STT configured as default provider', { source: 'useTranscriptions' })
      }
      else {
        hearingStore.activeTranscriptionProvider = 'browser-web-speech-api'
        console.info('BetterAgent WebSocket not connected; falling back to Web Speech API as default provider', { source: 'useTranscriptions' })
      }
    }

    // Check if streaming input is supported
    // TODO: implement non-streaming transcription
    if (!supportsStreamInput.value) {
      const errorMsg = 'Streaming input not supported by the selected transcription provider. Please select a provider that supports streaming (e.g., Web Speech API).'
      console.warn(errorMsg, { source: 'useTranscriptions' })
      // Clean up any existing sessions from other pages (e.g., test page) that might interfere
      await stopStreamingTranscription(true)
      isListening.value = false
      return
    }

    try {
      // Request microphone permission if needed (microphone should already be enabled by the user)
      if (!stream.value) {
        console.info('Requesting microphone permission', { source: 'useTranscriptions' })
        await askPermission()

        // If still no stream, try starting it manually
        if (!stream.value && hearingEnabled.value) {
          console.info('Attempting to start stream manually', { source: 'useTranscriptions' })
          startStream()
          // Wait for the stream to become available with a timeout.
          try {
            await until(stream).toBeTruthy({ timeout: 3000, throwOnTimeout: true })
          }
          catch {
            console.error('Timed out waiting for audio stream. Stopping transcription.', { source: 'useTranscriptions' })
            isListening.value = false
            return
          }
        }
      }
    }
    catch (err) {
      console.error('Failed to request microphone permission:', err, { source: 'useTranscriptions' })
      isListening.value = false
    }

    if (!stream.value) {
      const errorMsg = 'Failed to get audio stream for transcription. Please check microphone permissions and ensure a device is selected.'
      console.error(errorMsg, { source: 'useTranscriptions' })
      isListening.value = false
      return
    }

    console.info('Starting streaming transcription with stream:', stream.value.id, { source: 'useTranscriptions' })

    // Allow calling this even if already listening - transcribeForMediaStream will handle session reuse/restart
    // Call transcribeForMediaStream - it's async so we await it
    // Set listening state AFTER successful call
    try {
      await transcribeForMediaStream(stream.value, {
        onSentenceEnd: (delta) => {
          if (delta && delta.trim()) {
            console.info('Received transcription delta:', delta, { source: 'useTranscriptions' })
            // Append transcribed text to message input
            const currentText = messageInput.value.trim()
            messageInput.value = currentText ? `${currentText} ${delta}` : delta
            debouncedAutoSend()
          }
        },
        // Omit onSpeechEnd to avoid re-adding user-deleted text; use sentence deltas only.
      })

      // Only set listening to true if transcription started successfully
      // (transcribeForMediaStream might return early if session already exists)
      isListening.value = true
      console.info('Streaming transcription initiated successfully', { source: 'useTranscriptions' })
    }
    catch (err) {
      console.error('Transcription error:', err, { source: 'useTranscriptions' })
      isListening.value = false
      throw err
    }
  }

  // Watch for auto-send setting changes and clear pending sends if disabled
  watch(autoSendEnabled, (enabled) => {
    if (!enabled) {
      clearPendingAutoSend()
      console.info('Auto-send disabled', { source: 'useTranscriptions' })
    }
  })

  // Watch for auto-send setting changes and clear pending sends if disabled
  watch(hearingEnabled, async (enabled) => {
    if (!enabled) {
      await stopStreaming()
      console.info('Stopping streaming transcription because hearing is disabled.', { source: 'useTranscriptions' })
    }
  })

  onScopeDispose(() => {
    clearPendingAutoSend()
    stopStreaming()
  })

  return {
    startStreamingTranscription: startStreaming,
    stopStreamingTranscription: stopStreaming,
    isListening,
    autoSendEnabled,
  }
}
