/**
 * Taps the same AudioContext/source node VAD is already using (see
 * @proj-airi/stage-ui/stores/ai/models/vad's getAudioNodes()) to capture
 * 16kHz 16-bit PCM chunks for STT, gated to only forward audio between
 * speech_start and speech_end. A second, independent AudioContext would
 * each resample the mic stream on its own -- not guaranteed byte-identical,
 * which would let VAD's speech boundaries drift from the audio actually
 * sent to FunASR. AudioNodes support multiple downstream connections from
 * one source, so tapping in alongside VAD's own worklet doesn't disturb it.
 */
import { ref } from 'vue'

export interface STTAudioCaptureOptions {
  onChunk: (pcm: Int16Array) => void
}

const modulesLoadedFor = new WeakSet<AudioContext>()

export function useSTTAudioCapture(workletUrl: string, options: STTAudioCaptureOptions) {
  let workletNode: AudioWorkletNode | null = null
  const capturing = ref(false)
  const connected = ref(false)

  async function connect(audioContext: AudioContext, sourceNode: MediaStreamAudioSourceNode): Promise<void> {
    disconnect()

    if (!modulesLoadedFor.has(audioContext)) {
      await audioContext.audioWorklet.addModule(workletUrl)
      modulesLoadedFor.add(audioContext)
    }

    workletNode = new AudioWorkletNode(audioContext, 'stt-capture-worklet-processor')
    workletNode.port.onmessage = (event: MessageEvent<{ buffer?: ArrayBuffer }>) => {
      if (!capturing.value)
        return
      const { buffer } = event.data
      if (buffer)
        options.onChunk(new Int16Array(buffer))
    }

    sourceNode.connect(workletNode)
    connected.value = true
  }

  function disconnect(): void {
    if (workletNode) {
      workletNode.port.onmessage = null
      workletNode.disconnect()
      workletNode = null
    }
    connected.value = false
    capturing.value = false
  }

  function startCapturing(): void {
    capturing.value = true
  }

  function stopCapturing(): void {
    capturing.value = false
  }

  return {
    capturing,
    connected,
    connect,
    disconnect,
    startCapturing,
    stopCapturing,
  }
}
