/**
 * BetterAgent WebSocket Client Bridge
 * Connects Airi frontend directly to BetterAgent Go WebGateway (ws://localhost:8080/ws)
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

export type TextDeltaCallback = (text: string, isFinal: boolean, chatId?: number) => void
export type EmotionCallback = (emotion: string, action?: string) => void
export type AudioChunkCallback = (audioBase64: string, sampleRate: number, visemes?: Viseme[]) => void
export type StateChangeCallback = (state: string, chatId?: number) => void
export interface GameStatePayload {
  floor: number
  hp: number
  max_hp: number
  gold: number
  act: number
}
export type GameStateCallback = (state: GameStatePayload) => void

export interface EmotionalStatePayload {
  mood: 'HAPPY' | 'NEUTRAL' | 'MOODY' | 'SLEEPY' | 'JEALOUS' | string
  valence: number
  arousal: number
  energy: number
  social_battery: number
  affection: number
  is_jealous: boolean
  description: string
}
export type EmotionStateCallback = (state: EmotionalStatePayload, action?: string) => void

// Binary Audio Frame Protocol
function encodeBinaryAudioFrame(pcm: Int16Array): ArrayBuffer {
  const buf = new ArrayBuffer(20 + pcm.byteLength)
  const view = new DataView(buf)
  view.setUint8(0, 0x41) // 'A'
  view.setUint8(1, 0x55) // 'U'
  view.setUint8(2, 0x44) // 'D'
  view.setUint8(3, 0x49) // 'I'
  view.setBigInt64(4, 0n, false)
  view.setBigUint64(12, 0n, false)
  new Uint8Array(buf, 20).set(new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength))
  return buf
}

function uint8ArrayToBase64(bytes: Uint8Array): string {
  let binary = ''
  const len = bytes.byteLength
  const chunkSize = 0x8000 // 32KB chunking
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
  private emotionStateListeners: Set<EmotionStateCallback> = new Set()
  private audioChunkListeners: Set<AudioChunkCallback> = new Set()
  private stateChangeListeners: Set<StateChangeCallback> = new Set()
  private gameStateListeners: Set<GameStateCallback> = new Set()

  constructor(serverUrl = 'ws://localhost:8080/ws') {
    if (!serverUrl.includes('chat_id=')) {
      const defaultChatID = Math.floor(1000000 + Math.random() * 9000000)
      serverUrl += (serverUrl.includes('?') ? '&' : '?') + `chat_id=${defaultChatID}`
    }

    if (!serverUrl.includes('token=')) {
      const token = (typeof import.meta !== 'undefined' && import.meta.env?.VITE_BETTERAGENT_WS_TOKEN)
        ? import.meta.env.VITE_BETTERAGENT_WS_TOKEN
        : undefined
      if (token) {
        serverUrl += (serverUrl.includes('?') ? '&' : '?') + `token=${encodeURIComponent(token)}`
      }
    }

    this.url = serverUrl
  }

  public connect(): void {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return
    }
    this.isIntentionalClose = false
    try {
      this.ws = new WebSocket(this.url)
      this.ws.binaryType = 'arraybuffer'

      this.ws.onopen = () => {
        console.log('[BetterAgentWSBridge] Connected successfully 🚀')
        this.reconnectAttempts = 0
      }

      this.ws.onmessage = (event: MessageEvent) => {
        this.handleMessage(event)
      }

      this.ws.onerror = (err) => {
        console.warn('[BetterAgentWSBridge] WebSocket error:', err)
      }

      this.ws.onclose = () => {
        if (!this.isIntentionalClose) {
          this.scheduleReconnect()
        }
      }
    }
    catch (err) {
      console.error('[BetterAgentWSBridge] Failed to create WebSocket:', err)
      this.scheduleReconnect()
    }
  }

  private scheduleReconnect(): void {
    this.reconnectAttempts++
    const delay = Math.min(1000 * (2 ** this.reconnectAttempts), this.maxReconnectInterval)
    setTimeout(() => this.connect(), delay)
  }

  private handleMessage(event: MessageEvent): void {
    if (event.data instanceof ArrayBuffer) {
      this.handleBinaryFrame(event.data)
      return
    }

    try {
      const msg: WSMessage = JSON.parse(event.data)
      switch (msg.type) {
        case 'agent.text_delta':
          if (msg.payload?.text) {
            this.textDeltaListeners.forEach(cb => cb(msg.payload.text, !!msg.payload.is_final, msg.payload.chat_id))
          }
          break

        case 'agent.emotion':
          if (msg.payload) {
            const emotionStr = typeof msg.payload === 'string' ? msg.payload : (msg.payload.emotion || msg.payload.mood || '')
            if (emotionStr) {
              this.emotionListeners.forEach(cb => cb(emotionStr, msg.payload.action))
            }
            if (typeof msg.payload === 'object' && ('valence' in msg.payload || 'mood' in msg.payload)) {
              this.emotionStateListeners.forEach(cb => cb(msg.payload as EmotionalStatePayload, msg.payload.action))
            }
          }
          break

        case 'agent.game_state':
          if (msg.payload) {
            this.gameStateListeners.forEach(cb => cb(msg.payload))
          }
          break

        case 'agent.audio_chunk':
          break

        case 'agent.state_change':
          if (msg.payload?.state) {
            this.stateChangeListeners.forEach(cb => cb(msg.payload.state, msg.payload.chat_id))
          }
          break

        default:
          console.debug('[BetterAgentWSBridge] Unknown frame type:', msg.type)
      }
    }
    catch (err) {
      console.warn('[BetterAgentWSBridge] Error parsing JSON message:', err)
    }
  }

  private handleBinaryFrame(buf: ArrayBuffer): void {
    const view = new DataView(buf)
    if (buf.byteLength < 20)
      return

    const magic = String.fromCharCode(view.getUint8(0), view.getUint8(1), view.getUint8(2), view.getUint8(3))
    if (magic !== 'AUDI')
      return

    const rawAudio = buf.slice(20)
    const base64Audio = uint8ArrayToBase64(new Uint8Array(rawAudio))
    this.audioChunkListeners.forEach(cb => cb(base64Audio, 24000))
  }

  public sendUserText(text: string, chatId?: number): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      const msg: WSMessage = {
        type: 'user.text',
        payload: { text, chat_id: chatId },
      }
      this.ws.send(JSON.stringify(msg))
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

  public sendAudioChunk(pcm: Int16Array): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(encodeBinaryAudioFrame(pcm))
    }
  }

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

  public sendPersonaUpdate(personaId: string, patch: Record<string, unknown>): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      const msg: WSMessage = {
        type: 'admin.persona_update',
        payload: { persona_id: personaId, ...patch },
      }
      this.ws.send(JSON.stringify(msg))
    }
  }

  public onEmotionState(cb: EmotionStateCallback): () => void {
    this.emotionStateListeners.add(cb)
    return () => this.emotionStateListeners.delete(cb)
  }

  public onGameState(cb: GameStateCallback): () => void {
    this.gameStateListeners.add(cb)
    return () => this.gameStateListeners.delete(cb)
  }

  public disconnect(): void {
    this.isIntentionalClose = true
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
  }
}

export const betterAgentWSBridge = new BetterAgentWSBridge()
