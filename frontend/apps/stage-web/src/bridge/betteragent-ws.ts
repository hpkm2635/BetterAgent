/**
 * BetterAgent WebSocket Client Bridge
 * Connects Airi stage-web frontend directly to BetterAgent Go WebGateway (ws://localhost:8080/ws)
 */

export interface Viseme {
  time_offset: number
  viseme_id: number
  shape: string
}

export interface WSMessage<T = any> {
  type: string
  payload?: T
}

export type TextDeltaCallback = (text: string) => void
export type EmotionCallback = (emotion: string, action?: string) => void
export type AudioChunkCallback = (audioBase64: string, sampleRate: number, visemes?: Viseme[]) => void
export type StateChangeCallback = (state: string) => void

/**
 * Safely converts Uint8Array to base64 string without triggering V8 call stack size limit
 */
function uint8ArrayToBase64(bytes: Uint8Array): string {
  let binary = ''
  const len = bytes.byteLength
  const chunkSize = 0x8000 // 32KB chunking to prevent V8 argument stack overflow
  for (let i = 0; i < len; i += chunkSize) {
    const chunk = bytes.subarray(i, Math.min(i + chunkSize, len))
    binary += String.fromCharCode.apply(null, chunk as unknown as number[])
  }
  return btoa(binary)
}

export class BetterAgentWSBridge {
  private ws: WebSocket | null = null
  private url: string
  private reconnectAttempts = 0
  private maxReconnectInterval = 5000
  private isIntentionalClose = false

  private textDeltaListeners: Set<TextDeltaCallback> = new Set()
  private emotionListeners: Set<EmotionCallback> = new Set()
  private audioChunkListeners: Set<AudioChunkCallback> = new Set()
  private stateChangeListeners: Set<StateChangeCallback> = new Set()

  constructor(serverUrl = 'ws://localhost:8080/ws') {
    // Append default or random chat_id if not present
    if (!serverUrl.includes('chat_id=')) {
      const defaultChatID = Math.floor(1000000 + Math.random() * 9000000)
      serverUrl += (serverUrl.includes('?') ? '&' : '?') + `chat_id=${defaultChatID}`
    }
    this.url = serverUrl
  }

  public connect(): void {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      console.log('[BetterAgentWSBridge] Already connected or connecting, skipping duplicate connect()')
      return
    }
    this.isIntentionalClose = false
    try {
      console.log(`[BetterAgentWSBridge] Connecting to ${this.url}...`)
      this.ws = new WebSocket(this.url)
      this.ws.binaryType = 'arraybuffer'

      this.ws.onopen = () => {
        console.log('[BetterAgentWSBridge] WebSocket Connected successfully 🚀')
        this.reconnectAttempts = 0
      }

      this.ws.onmessage = (event: MessageEvent) => {
        this.handleMessage(event)
      }

      this.ws.onerror = (err) => {
        console.warn('[BetterAgentWSBridge] WebSocket error:', err)
      }

      this.ws.onclose = () => {
        console.warn('[BetterAgentWSBridge] WebSocket connection closed')
        if (!this.isIntentionalClose) {
          this.scheduleReconnect()
        }
      }
    } catch (err) {
      console.error('[BetterAgentWSBridge] Failed to create WebSocket:', err)
      this.scheduleReconnect()
    }
  }

  private scheduleReconnect(): void {
    this.reconnectAttempts++
    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), this.maxReconnectInterval)
    console.log(`[BetterAgentWSBridge] Reconnecting in ${delay}ms (Attempt ${this.reconnectAttempts})...`)
    setTimeout(() => this.connect(), delay)
  }

  private handleMessage(event: MessageEvent): void {
    // 1. Binary Frame Handling (Zero-Copy Audio Binary)
    if (event.data instanceof ArrayBuffer) {
      this.handleBinaryFrame(event.data)
      return
    }

    // 2. Text JSON Frame Handling
    try {
      const msg: WSMessage = JSON.parse(event.data)
      switch (msg.type) {
        case 'agent.text_delta':
          if (msg.payload?.text) {
            this.textDeltaListeners.forEach(cb => cb(msg.payload.text))
          }
          break

        case 'agent.emotion':
          if (msg.payload?.emotion) {
            this.emotionListeners.forEach(cb => cb(msg.payload.emotion, msg.payload.action))
          }
          break

        case 'agent.audio_chunk':
          if (msg.payload?.audio_base64) {
            this.audioChunkListeners.forEach(cb => cb(
              msg.payload.audio_base64,
              msg.payload.sample_rate || 24000,
              msg.payload.visemes
            ))
          }
          break

        case 'agent.state_change':
          if (msg.payload?.state) {
            this.stateChangeListeners.forEach(cb => cb(msg.payload.state))
          }
          break

        default:
          console.debug('[BetterAgentWSBridge] Unknown frame type:', msg.type)
      }
    } catch (err) {
      console.warn('[BetterAgentWSBridge] Error parsing JSON message:', err)
    }
  }

  private handleBinaryFrame(buf: ArrayBuffer): void {
    const view = new DataView(buf)
    if (buf.byteLength < 20) return

    // Check Magic Header 'AUDI'
    const magic = String.fromCharCode(view.getUint8(0), view.getUint8(1), view.getUint8(2), view.getUint8(3))
    if (magic !== 'AUDI') return

    // High performance binary frame: Extract raw PCM/Opus slice
    const rawAudio = buf.slice(20)
    // Convert ArrayBuffer slice to base64 safely without V8 stack overflow
    const base64Audio = uint8ArrayToBase64(new Uint8Array(rawAudio))
    this.audioChunkListeners.forEach(cb => cb(base64Audio, 24000))
  }

  // --- Outbound Triggers (Frontend -> WebGateway) ---

  public sendUserText(text: string, chatId?: number): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      const msg: WSMessage = {
        type: 'user.text',
        payload: { text, chat_id: chatId },
      }
      this.ws.send(JSON.stringify(msg))
    } else {
      console.warn('[BetterAgentWSBridge] Cannot send text, WebSocket not OPEN')
    }
  }

  public sendSpeechStart(): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'user.speech_start' }))
    }
  }

  public sendSpeechEnd(): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'user.speech_end' }))
    }
  }

  public sendVisionFrame(imageBase64: string, sourceType: 'screen' | 'camera' = 'screen', format = 'jpeg'): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      const msg: WSMessage = {
        type: 'user.vision_frame',
        payload: {
          image_base64: imageBase64,
          source_type: sourceType,
          format,
        },
      }
      this.ws.send(JSON.stringify(msg))
    }
  }

  // --- Event Listener Subscriptions ---

  public onTextDelta(cb: TextDeltaCallback): () => void {
    this.textDeltaListeners.add(cb)
    return () => this.textDeltaListeners.delete(cb)
  }

  public onEmotion(cb: EmotionCallback): () => void {
    this.emotionListeners.add(cb)
    return () => this.emotionListeners.delete(cb)
  }

  public onAudioChunk(cb: AudioChunkCallback): () => void {
    this.audioChunkListeners.add(cb)
    return () => this.audioChunkListeners.delete(cb)
  }

  public onStateChange(cb: StateChangeCallback): () => void {
    this.stateChangeListeners.add(cb)
    return () => this.stateChangeListeners.delete(cb)
  }

  public disconnect(): void {
    this.isIntentionalClose = true
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
  }
}

// Global Singleton Instance
export const betterAgentWSBridge = new BetterAgentWSBridge()

if (typeof window !== 'undefined') {
  ;(window as any).__betterAgentWSBridge = betterAgentWSBridge
}
