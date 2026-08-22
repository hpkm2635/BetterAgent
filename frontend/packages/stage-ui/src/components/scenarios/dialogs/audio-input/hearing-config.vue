<script setup lang="ts">
import { Callout, FieldCheckbox, FieldCombobox } from '@proj-airi/ui'
import { storeToRefs } from 'pinia'
import { computed, ref } from 'vue'

import { useAudioAnalyzer } from '../../../../composables'
import { useSettingsAudioDevice } from '../../../../stores'

const props = withDefaults(defineProps<{
  granted?: boolean // permission status on OS level
}>(), {
  granted: false,
})

const emit = defineEmits<{
  'toggle-transcription': []
}>()

const deviceStore = useSettingsAudioDevice()
const { askPermission } = deviceStore
const { audioInputs, enabled, permissionGranted, selectedAudioInput } = storeToRefs(deviceStore)
const { volumeLevel } = useAudioAnalyzer()

const autoSend = defineModel<boolean | undefined>('autoSend')
const requestingPermission = ref(false)
const permissionError = ref('')
const hasAutoSendControl = computed(() => autoSend.value !== undefined)
const autoSendEnabled = computed({
  get: () => autoSend.value ?? false,
  set: value => autoSend.value = value,
})
const hearingToggleLabel = computed(() => enabled.value ? 'Disable microphone input' : 'Enable microphone input')
const hearingToggleClass = computed(() => [
  'absolute left-1/2 top-1/2 grid h-16 w-16 place-items-center outline-none -translate-x-1/2 -translate-y-1/2',
  'rounded-2xl backdrop-blur-md',
  'transition-colors duration-150 ease-in-out',
  'focus-visible:ring-2 focus-visible:ring-primary-400/40',
  'active:scale-95',
  enabled.value
    ? [
        'border-2 border-solid border-primary-300/30 bg-primary-200/90~45 text-primary-950',
        'hover:border-primary-400/55 active:border-primary-500/60',
        'dark:border-primary-400/30 dark:bg-primary-500/75~45*90 dark:text-primary-50',
        'dark:hover:border-primary-300/55 dark:active:border-primary-300/70',
      ]
    : [
        'border-2 border-solid border-neutral-300/40 bg-neutral-100/70 text-neutral-700',
        'hover:border-neutral-400/55 active:border-neutral-500/60',
        'dark:border-neutral-700/45 dark:bg-neutral-800/70 dark:text-neutral-200',
        'dark:hover:border-neutral-600/65 dark:active:border-neutral-500/70',
      ],
])
const ringEnabledClass = computed(() => enabled.value
  ? 'bg-primary-500/15 dark:bg-primary-600/20'
  : 'bg-neutral-300/20 dark:bg-neutral-700/20',
)

function microphoneDeniedMessage(error: unknown): string {
  const name = error instanceof DOMException ? error.name : 'Error'
  if (typeof window !== 'undefined' && window.self !== window.top)
    return 'Microphone is blocked inside this embedded preview. Open http://localhost:5173 in a new browser tab and try again.'
  if (typeof window !== 'undefined' && window.isSecureContext === false)
    return 'Microphone requires a secure context. Open http://localhost:5173 (or use HTTPS) and try again.'
  if (name === 'NotFoundError' || name === 'OverconstrainedError')
    return 'No microphone device was found. Connect a microphone and try again.'
  return `Microphone access was denied (${name}). Enable it in your browser site settings and try again.`
}

async function toggleHearingEnabled() {
  if (enabled.value) {
    enabled.value = false
    emit('toggle-transcription')
    return
  }

  if (selectedAudioInput.value !== '' && permissionGranted.value) {
    enabled.value = true
    emit('toggle-transcription')
    return
  }

  if (!permissionGranted.value) {
    const ok = await ensureMicrophonePermission()
    if (ok) {
      enabled.value = true
      emit('toggle-transcription')
    }
  }
}

async function ensureMicrophonePermission() {
  if (requestingPermission.value)
    return permissionGranted.value || audioInputs.value.length > 0
  requestingPermission.value = true
  permissionError.value = ''

  try {
    await askPermission()
  }
  catch {
    // Fall through to the direct getUserMedia fallback below.
  }

  if (!permissionGranted.value && audioInputs.value.length === 0) {
    const mediaDevices = typeof navigator !== 'undefined' ? navigator.mediaDevices : undefined
    if (mediaDevices?.getUserMedia) {
      try {
        const stream = await mediaDevices.getUserMedia({ audio: true })
        stream.getTracks().forEach(track => track.stop())
        await askPermission()
      }
      catch (error) {
        permissionError.value = microphoneDeniedMessage(error)
      }
    }
    else {
      permissionError.value = 'Microphone requires a secure context. Open http://localhost:5173 (or use HTTPS) and try again.'
    }
  }

  if (permissionGranted.value || audioInputs.value.length > 0)
    permissionError.value = ''

  requestingPermission.value = false
  return permissionGranted.value || audioInputs.value.length > 0
}

async function requestMicrophonePermission() {
  if (requestingPermission.value)
    return
  try {
    await ensureMicrophonePermission()
  }
  finally {
    requestingPermission.value = false
  }
}
</script>

<template>
  <div class="space-y-2">
    <!-- Minimal mic control with animated rings -->
    <div class="flex flex-col items-center justify-center py-2">
      <div class="relative h-28 w-28 select-none">
        <!-- Rings (scale + opacity follow volume) -->
        <div
          class="absolute left-1/2 top-1/2 h-20 w-20 rounded-full transition-all duration-150 -translate-x-1/2 -translate-y-1/2"
          :style="{ transform: `translate(-50%, -50%) scale(${1 + (volumeLevel / 100) * 0.35})`, opacity: String(0.25 + (volumeLevel / 100) * 0.25) }"
          :class="ringEnabledClass"
        />
        <div
          class="absolute left-1/2 top-1/2 h-24 w-24 rounded-full transition-all duration-200 -translate-x-1/2 -translate-y-1/2"
          :style="{ transform: `translate(-50%, -50%) scale(${1.2 + (volumeLevel / 100) * 0.55})`, opacity: String(0.15 + (volumeLevel / 100) * 0.2) }"
          :class="enabled ? 'bg-primary-500/10 dark:bg-primary-600/15' : 'bg-neutral-300/10 dark:bg-neutral-700/10'"
        />
        <div
          class="absolute left-1/2 top-1/2 h-28 w-28 rounded-full transition-all duration-300 -translate-x-1/2 -translate-y-1/2"
          :style="{ transform: `translate(-50%, -50%) scale(${1.5 + (volumeLevel / 100) * 0.8})`, opacity: String(0.08 + (volumeLevel / 100) * 0.15) }"
          :class="enabled ? 'bg-primary-500/5 dark:bg-primary-600/10' : 'bg-neutral-300/5 dark:bg-neutral-700/5'"
        />

        <!-- Mic icon button -->
        <button
          :aria-label="hearingToggleLabel"
          :title="hearingToggleLabel"
          :class="hearingToggleClass"
          @click="toggleHearingEnabled"
        >
          <div :class="enabled ? 'i-ph:microphone' : 'i-ph:microphone-slash'" class="h-6 w-6" />
        </button>
      </div>

      <div class="mt-3 h-1" />

      <!-- Permission callout when needed (Electron contexts) -->
      <div v-if="!props.granted" class="mt-3 w-full">
        <Callout theme="orange" label="Microphone permission required">
          <div class="text-sm">
            The app doesn't have permission to access your microphone.
            Please grant microphone access in your system settings to enable audio input.
          </div>
        </Callout>
      </div>
    </div>

    <div v-if="hasAutoSendControl" class="mt-3">
      <FieldCheckbox
        v-model="autoSendEnabled"
        label="Auto send"
        description="Send transcribed text to chat automatically."
      />
    </div>

    <!-- Always-visible device selector -->
    <div class="mt-3 w-full">
      <FieldCombobox
        v-model="selectedAudioInput"
        label="Input device"
        description="Select the microphone you want to use."
        :options="audioInputs.map(device => ({ label: device.label || 'Unknown Device', value: device.deviceId }))"
        placeholder="Select microphone"
        layout="vertical"
      />
      <button
        v-if="audioInputs.length === 0"
        class="mt-2 w-full rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-700 outline-none transition-colors hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200 dark:hover:bg-neutral-700"
        :disabled="requestingPermission"
        @click="requestMicrophonePermission"
      >
        {{ requestingPermission ? 'Requesting access…' : 'Grant microphone access' }}
      </button>
      <p v-if="permissionError" class="mt-2 text-sm text-red-500">{{ permissionError }}</p>
    </div>
  </div>
</template>
