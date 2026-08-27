<script setup lang="ts">
import type { ChatProvider } from '@xsai-ext/providers/utils'

import Header from '@proj-airi/stage-layouts/components/Layouts/Header.vue'
import InteractiveArea from '@proj-airi/stage-layouts/components/Layouts/InteractiveArea.vue'
import MobileHeader from '@proj-airi/stage-layouts/components/Layouts/MobileHeader.vue'
import MobileInteractiveArea from '@proj-airi/stage-layouts/components/Layouts/MobileInteractiveArea.vue'
import workletUrl from '@proj-airi/stage-ui/workers/vad/process.worklet?worker&url'
import sttCaptureWorkletUrl from '@proj-airi/stage-ui/workers/stt-capture/process.worklet?worker&url'

import { BackgroundProvider } from '@proj-airi/stage-layouts/components/Backgrounds'
import { useBackgroundThemeColor } from '@proj-airi/stage-layouts/composables/theme-color'
import { useBackgroundStore } from '@proj-airi/stage-layouts/stores/background'
import { HoloCoupon } from '@proj-airi/stage-ui/components'
import { LiveCaptionOverlay, STS2Dashboard } from '@proj-airi/stage-ui/components/gadgets'
import { ViewControlSlider, WidgetStage } from '@proj-airi/stage-ui/components/scenes'
import { useAudioRecorder } from '@proj-airi/stage-ui/composables/audio/audio-recorder'
import { useVAD } from '@proj-airi/stage-ui/stores/ai/models/vad'
import { useChatOrchestratorStore } from '@proj-airi/stage-ui/stores/chat'
import { useBetterAgentGatewayStore } from '@proj-airi/stage-ui/stores/modules/betteragent-gateway'
import { useConsciousnessStore } from '@proj-airi/stage-ui/stores/modules/consciousness'
import { useHearingSpeechInputPipeline } from '@proj-airi/stage-ui/stores/modules/hearing'
import { useSTS2GameStateStore } from '@proj-airi/stage-ui/stores/modules/sts2-game-state'
import { useProvidersStore } from '@proj-airi/stage-ui/stores/providers'
import { useSettings, useSettingsAudioDevice } from '@proj-airi/stage-ui/stores/settings'
import { breakpointsTailwind, useBreakpoints, useMouse } from '@vueuse/core'
import { storeToRefs } from 'pinia'
import { computed, onMounted, onUnmounted, ref, useTemplateRef, watch } from 'vue'

const paused = ref(false)

function handleSettingsOpen(open: boolean) {
  paused.value = open
}

const breakpoints = useBreakpoints(breakpointsTailwind)
const isMobile = breakpoints.smaller('md')

const backgroundStore = useBackgroundStore()
const { selectedOption, sampledColor } = storeToRefs(backgroundStore)
const backgroundSurface = useTemplateRef<InstanceType<typeof BackgroundProvider>>('backgroundSurface')
const { stageModelRenderer } = storeToRefs(useSettings())

const { syncBackgroundTheme } = useBackgroundThemeColor({ backgroundSurface, selectedOption, sampledColor })
onMounted(() => syncBackgroundTheme())

// Audio + transcription pipeline (mirrors stage-tamagotchi)
const settingsAudioDeviceStore = useSettingsAudioDevice()
const { stream, enabled } = storeToRefs(settingsAudioDeviceStore)
const { startRecord, stopRecord, onStopRecord } = useAudioRecorder(stream)
const hearingPipeline = useHearingSpeechInputPipeline()
const { transcribeForRecording } = hearingPipeline
const { supportsStreamInput } = storeToRefs(hearingPipeline)
const providersStore = useProvidersStore()
const consciousnessStore = useConsciousnessStore()
const { activeProvider: activeChatProvider, activeModel: activeChatModel } = storeToRefs(consciousnessStore)
const chatStore = useChatOrchestratorStore()

const shouldUseStreamInput = computed(() => supportsStreamInput.value && !!stream.value)

const {
  init: initVAD,
  dispose: disposeVAD,
  start: startVAD,
  loaded: vadLoaded,
  getAudioNodes: getVADAudioNodes,
} = useVAD(workletUrl, {
  threshold: ref(0.6),
  onSpeechStart: () => handleSpeechStart(),
  onSpeechEnd: () => handleSpeechEnd(),
})

let stopOnStopRecord: (() => void) | undefined

import { betterAgentWSBridge } from '../bridge/betteragent-ws'
import { useSTTAudioCapture } from '../composables/stt-audio-capture'

// Expose the bridge as window.__betterAgentWSBridge so that Stage.vue's
// setInterval audio-chunk poller and chat.ts's streamWithStageAdapters
// cutover guard can both find the same singleton. Without this assignment
// Stage.vue never hooks audio and chat.ts always falls through to the xsai
// provider, bypassing the entire Go WebGateway + NATS pipeline.
if (typeof window !== 'undefined') {
  ;(window as any).__betterAgentWSBridge = betterAgentWSBridge
}
betterAgentWSBridge.connect()

// True when the BetterAgent Go WebGateway bridge owns this session (see
// stores/chat.ts's streamWithStageAdapters doing the same window.__betterAgentWSBridge
// check for text) -- when it does, voice goes through the FunASR streaming
// pipeline below instead of Airi's own local record-then-transcribe-once flow
// or its provider-native streaming input, both of which would otherwise also
// try to submit the same utterance.
function isBetterAgentBridgeActive(): boolean {
  return typeof window !== 'undefined' && !!(window as any).__betterAgentWSBridge
}

const sttAudioCapture = useSTTAudioCapture(sttCaptureWorkletUrl, {
  onChunk: pcm => betterAgentWSBridge.sendAudioChunk(pcm),
})

async function connectSTTCapture() {
  const nodes = getVADAudioNodes()
  if (!nodes) {
    // Previously silent -- VAD's audio nodes not being ready yet means the
    // STT capture worklet never connects, and every utterance for the rest
    // of this session would send 0 bytes of audio with no other visible
    // symptom until services/stt's logs are checked.
    console.error('[STT Capture] getVADAudioNodes() returned undefined -- STT capture worklet was not connected this attempt.')
    return
  }
  try {
    await sttAudioCapture.connect(nodes.audioContext, nodes.sourceNode)
  }
  catch (e) {
    console.error('Failed to connect STT audio capture:', e)
  }
}

async function startAudioInteraction() {
  try {
    await initVAD()
    if (stream.value) {
      await startVAD(stream.value)
      await connectSTTCapture()
    }

    // Hook once
    stopOnStopRecord = onStopRecord(async (recording) => {
      const text = await transcribeForRecording(recording)
      if (!text || !text.trim())
        return

      try {
        const provider = await providersStore.getProviderInstance(activeChatProvider.value)
        if (!provider || !activeChatModel.value)
          return

        await chatStore.ingest(text, { model: activeChatModel.value, chatProvider: provider as ChatProvider })
      }
      catch (err) {
        console.error('Failed to send chat from voice:', err)
      }
    })
  }
  catch (e) {
    console.error('Audio interaction init failed:', e)
  }
}

async function handleSpeechStart() {
  // Trigger Barge-in cancel on BetterAgent WebSocket Gateway
  betterAgentWSBridge.sendSpeechStart()

  if (isBetterAgentBridgeActive()) {
    sttAudioCapture.startCapturing()
    return
  }

  if (shouldUseStreamInput.value) {
    return
  }

  startRecord()
}

async function handleSpeechEnd() {
  betterAgentWSBridge.sendSpeechEnd()

  if (isBetterAgentBridgeActive()) {
    sttAudioCapture.stopCapturing()
    return
  }

  if (shouldUseStreamInput.value) {
    return
  }

  stopRecord()
}

function stopAudioInteraction() {
  try {
    stopOnStopRecord?.()
    stopOnStopRecord = undefined
    sttAudioCapture.disconnect()
    disposeVAD()
  }
  catch {}
}

watch(enabled, async (val) => {
  if (val) {
    await startAudioInteraction()
  }
  else {
    stopAudioInteraction()
  }
}, { immediate: true })

onUnmounted(() => {
  stopAudioInteraction()
  betterAgentWSBridge.disconnect()
  if (typeof window !== 'undefined') {
    delete (window as any).__betterAgentWSBridge
  }
})

watch([stream, () => vadLoaded.value], async ([s, loaded]) => {
  if (enabled.value && loaded && s) {
    try {
      await startVAD(s)
      await connectSTTCapture()
    }
    catch (e) {
      console.error('Failed to start VAD with stream:', e)
    }
  }
})

const sts2GameStateStore = useSTS2GameStateStore()
const { isLikelyActive: isGameActive } = storeToRefs(sts2GameStateStore)
const gatewayStore = useBetterAgentGatewayStore()
const activeChatId = typeof window !== 'undefined' ? Number(new URLSearchParams(window.location.search).get('chat_id') || 0) || undefined : undefined
gatewayStore.initialize(activeChatId)

const { x: mouseX, y: mouseY } = useMouse()
const cursorPosition = computed(() => {
  if (isGameActive.value && !isMobile.value && typeof window !== 'undefined') {
    // Game streamer mode on desktop: tilt head/gaze toward game window on the right
    return {
      x: window.innerWidth * 0.88,
      y: window.innerHeight * 0.5,
    }
  }
  return {
    x: mouseX.value,
    y: mouseY.value,
  }
})
</script>

<template>
  <BackgroundProvider
    ref="backgroundSurface"
    class="widgets top-widgets"
    :background="selectedOption"
    :top-color="sampledColor"
  >
    <div relative flex="~ col" z-2 h-100dvh w-100vw of-hidden>
      <!-- header -->
      <div class="px-0 py-1 md:px-3 md:py-3" w-full gap-2>
        <Header class="hidden md:flex" />
        <MobileHeader class="flex md:hidden" />
      </div>
      <!-- page -->
      <div relative flex="~ 1 row gap-y-0 gap-x-2 <md:col">
        <div relative flex-1 min-w="1/2">
          <div
            absolute left-0 z-15 px-3
            :class="[
              stageModelRenderer === 'live2d' ? 'top-0 h-full py-[20vh]' : 'top-1/2 -translate-y-1/2',
            ]"
          >
            <ViewControlSlider />
          </div>
          <WidgetStage
            h-full w-full
            :cursor-position="cursorPosition"
            :enable-orbit-controls="!isMobile"
            :paused="paused"
          />
          <!-- AI Game Streamer Gadgets -->
          <LiveCaptionOverlay />
          <STS2Dashboard />
        </div>
        <InteractiveArea v-if="!isMobile" h="85dvh" absolute right-4 flex flex-1 flex-col max-w="500px" min-w="30%" />
        <MobileInteractiveArea v-if="isMobile" @settings-open="handleSettingsOpen" />
      </div>
      <HoloCoupon />
    </div>
  </BackgroundProvider>
</template>

<route lang="yaml">
name: IndexScenePage
meta:
  layout: stage
  stageTransition:
    name: bubble-wave-out
</route>
