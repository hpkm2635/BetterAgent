// stt-capture-worklet-processor.ts
// This file needs to be registered as an AudioWorklet.
//
// Converts captured mic samples (Float32, whatever rate the AudioContext
// this is attached to runs at -- 16kHz when attached to VAD's context, see
// composables/audio/stt-capture.ts) into 16-bit PCM and batches them into
// ~100ms chunks before posting to the main thread. Deliberately small: a
// bigger batch here is pure self-inflicted latency added on top of whatever
// FunASR's own inference takes, unrelated to how fast FunASR itself can
// transcribe.
const CHUNK_DURATION_MS = 100

function floatTo16BitPCM(sample: number): number {
  const clamped = Math.max(-1, Math.min(1, sample))
  return clamped < 0 ? clamped * 0x8000 : clamped * 0x7FFF
}

class STTCaptureProcessor extends AudioWorkletProcessor {
  private readonly chunkSize: number
  private buffer: Int16Array
  private pointer = 0

  constructor() {
    super()
    this.chunkSize = Math.max(1, Math.floor(sampleRate * CHUNK_DURATION_MS / 1000))
    this.buffer = new Int16Array(this.chunkSize)
  }

  process(inputs: Float32Array[][]): boolean {
    const input = inputs[0]?.[0]
    if (!input)
      return true // buffer is null when the stream ends

    for (let i = 0; i < input.length; i++) {
      this.buffer[this.pointer] = floatTo16BitPCM(input[i])
      this.pointer++
      if (this.pointer >= this.buffer.length) {
        const outBuffer = this.buffer.buffer
        this.port.postMessage({ buffer: outBuffer }, [outBuffer])
        // this.buffer.buffer was just transferred (and synchronously
        // detached) by the postMessage call above, so this.buffer.length is
        // now 0 -- must size the replacement from the stored chunkSize, not
        // by reading the just-detached buffer's own length.
        this.buffer = new Int16Array(this.chunkSize)
        this.pointer = 0
      }
    }

    return true
  }
}

registerProcessor('stt-capture-worklet-processor', STTCaptureProcessor)
