import { ref, onUnmounted } from 'vue'
import { betterAgentWSBridge } from '../bridge/betteragent-ws'

export type VisionSource = 'screen' | 'camera'

export function useVision() {
  const isVisionActive = ref(false)
  const currentSource = ref<VisionSource>('screen')
  const statusMessage = ref('Vision System Idle')

  let mediaStream: MediaStream | null = null
  let videoEl: HTMLVideoElement | null = null
  let captureCanvas: HTMLCanvasElement | null = null
  let diffCanvas: HTMLCanvasElement | null = null
  let prevFramePixels: Uint8ClampedArray | null = null
  let captureIntervalTimer: any = null

  // Capture interval in MS (default 3 seconds)
  const CAPTURE_INTERVAL_MS = 3000
  // Frame Difference Threshold (Lowered to 1.2% so IDE typing & static window changes pass)
  const DIFF_THRESHOLD = 0.012
  // Target downscaled width for LLM Vision input
  const TARGET_WIDTH = 800
  // Downscaled size for fast Frame Difference Detection
  const DIFF_SIZE = 64

  async function startVision(source: VisionSource = 'screen') {
    stopVision()

    try {
      if (source === 'screen') {
        mediaStream = await navigator.mediaDevices.getDisplayMedia({
          video: { frameRate: { max: 5 } },
          audio: false,
        })
      } else {
        mediaStream = await navigator.mediaDevices.getUserMedia({
          video: { width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: false,
        })
      }

      currentSource.value = source

      // Create hidden video element to render stream frames
      videoEl = document.createElement('video')
      videoEl.autoplay = true
      videoEl.muted = true
      videoEl.srcObject = mediaStream
      await videoEl.play()

      // Create offscreen canvas for snapshot JPEG generation
      captureCanvas = document.createElement('canvas')
      diffCanvas = document.createElement('canvas')
      diffCanvas.width = DIFF_SIZE
      diffCanvas.height = DIFF_SIZE

      isVisionActive.value = true
      statusMessage.value = `Vision Active (${source})`
      console.log(`[useVision] Started vision stream: ${source}`)

      // Register global force capture trigger for user chat prompts
      if (typeof window !== 'undefined') {
        (window as any).__triggerVisionForceCapture = () => {
          console.log('[useVision] 📸 User prompt force capture triggered!')
          captureAndEvaluateFrame(true)
        }
      }

      // Handle stream end (e.g. user stops browser screen sharing)
      mediaStream.getVideoTracks()[0].onended = () => {
        console.log('[useVision] Media stream ended by user')
        stopVision()
      }

      // Perform initial snapshot immediately with force = true
      captureAndEvaluateFrame(true)

      // Start periodic capture timer
      captureIntervalTimer = setInterval(() => {
        captureAndEvaluateFrame(false)
      }, CAPTURE_INTERVAL_MS)

    } catch (err) {
      console.error('[useVision] Failed to start vision capture:', err)
      statusMessage.value = 'Vision Permission Denied / Error'
      stopVision()
    }
  }

  function stopVision() {
    if (captureIntervalTimer) {
      clearInterval(captureIntervalTimer)
      captureIntervalTimer = null
    }

    if (mediaStream) {
      mediaStream.getTracks().forEach(t => t.stop())
      mediaStream = null
    }

    if (videoEl) {
      videoEl.pause()
      videoEl.srcObject = null
      videoEl = null
    }

    if (typeof window !== 'undefined') {
      delete (window as any).__triggerVisionForceCapture
    }

    prevFramePixels = null
    isVisionActive.value = false
    statusMessage.value = 'Vision Off'
    console.log('[useVision] Vision capture stopped')
  }

  function toggleVision(source: VisionSource = 'screen') {
    if (isVisionActive.value) {
      stopVision()
    } else {
      startVision(source)
    }
  }

  /**
   * Captures frame, checks Frame Difference (>1.2%), and sends to WebGateway if changed or forced!
   */
  function captureAndEvaluateFrame(force = false) {
    if (!videoEl || !captureCanvas || !diffCanvas || !isVisionActive.value) return
    if (videoEl.readyState < 2) return // HAVE_CURRENT_DATA

    const vw = videoEl.videoWidth
    const vh = videoEl.videoHeight
    if (!vw || !vh) return

    // 1. Frame Difference Detection (Diff check at 64x64 resolution)
    const diffCtx = diffCanvas.getContext('2d', { willReadFrequently: true })
    if (!diffCtx) return

    diffCtx.drawImage(videoEl, 0, 0, DIFF_SIZE, DIFF_SIZE)
    const currentImgData = diffCtx.getImageData(0, 0, DIFF_SIZE, DIFF_SIZE)
    const currentPixels = currentImgData.data

    // Black Screen / DRM Protection Check (Average Brightness < 5)
    let totalLuminance = 0
    const sampleLen = currentPixels.length
    for (let i = 0; i < sampleLen; i += 4) {
      totalLuminance += (currentPixels[i] + currentPixels[i + 1] + currentPixels[i + 2]) / 3
    }
    const avgLuminance = totalLuminance / (sampleLen / 4)

    if (avgLuminance < 5) {
      console.warn(`[useVision] 🖤 DRM protected screen or pure black image detected (Avg Luminance: ${avgLuminance.toFixed(1)} < 5), skipping push`)
      statusMessage.value = 'DRM受保护画面/黑屏'
      return
    }

    if (!force && prevFramePixels) {
      let totalDiff = 0
      const totalPixels = DIFF_SIZE * DIFF_SIZE

      for (let i = 0; i < currentPixels.length; i += 4) {
        // Compute L1 RGB color distance
        const rDiff = Math.abs(currentPixels[i] - prevFramePixels[i])
        const gDiff = Math.abs(currentPixels[i + 1] - prevFramePixels[i + 1])
        const bDiff = Math.abs(currentPixels[i + 2] - prevFramePixels[i + 2])
        totalDiff += (rDiff + gDiff + bDiff) / (3 * 255)
      }

      const diffRatio = totalDiff / totalPixels

      if (diffRatio < DIFF_THRESHOLD) {
        console.debug(`[useVision] ⏸️ Static frame detected (Diff: ${(diffRatio * 100).toFixed(2)}% < 1.2%), skipping push to save bandwidth`)
        statusMessage.value = `Watching (Static: ${(diffRatio * 100).toFixed(1)}% diff)`
        return
      }

      console.log(`[useVision] 📸 Frame change detected (Diff: ${(diffRatio * 100).toFixed(2)}%), pushing to WebGateway...`)
    } else if (force) {
      console.log('[useVision] ⚡ Forced snapshot capture triggered!')
    }

    // Save current frame pixels for next comparison
    prevFramePixels = new Uint8ClampedArray(currentPixels)

    // 2. High-quality downscaled snapshot for Multimodal LLM Vision
    const scale = TARGET_WIDTH / vw
    const targetHeight = Math.round(vh * scale)

    captureCanvas.width = TARGET_WIDTH
    captureCanvas.height = targetHeight

    const capCtx = captureCanvas.getContext('2d')
    if (!capCtx) return

    capCtx.drawImage(videoEl, 0, 0, TARGET_WIDTH, targetHeight)

    // Export as JPEG with 0.6 quality
    const dataUrl = captureCanvas.toDataURL('image/jpeg', 0.6)
    const base64Data = dataUrl.replace(/^data:image\/jpeg;base64,/, '')

    // Send to BetterAgent Go WebGateway via WebSocket
    betterAgentWSBridge.sendVisionFrame(base64Data, currentSource.value, 'jpeg')
    statusMessage.value = `Vision Active (Frame Sent)`
  }

  onUnmounted(() => {
    stopVision()
  })

  return {
    isVisionActive,
    currentSource,
    statusMessage,
    startVision,
    stopVision,
    toggleVision,
    captureAndEvaluateFrame,
  }
}
