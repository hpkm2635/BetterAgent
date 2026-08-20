<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const router = useRouter()
const route = useRoute()

const loading = ref(false)
const errorMsg = ref('')
const form = reactive({ username: '', password: '' })

function fakeLogin() {
  localStorage.setItem('betteragent/local-auth', '1')
}

function submit() {
  errorMsg.value = ''
  if (!form.username.trim() || !form.password.trim()) {
    errorMsg.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  setTimeout(() => {
    fakeLogin()
    router.replace((route.query.redirect as string) || '/')
  }, 450)
}

// ---------------------------------------------------------------------------
// Particle name effect — "BETTER AGENT" made of particles.
// Optimized: snappier mouse response (less latency) + organic (non-circular)
// scatter direction via a per-particle random offset.
// ---------------------------------------------------------------------------
const canvasRef = ref<HTMLCanvasElement | null>(null)
const wrapRef = ref<HTMLElement | null>(null)
const fallback = ref(false)

interface Particle {
  x: number
  y: number
  hx: number
  hy: number
  vx: number
  vy: number
  s: number
  c: string
  r: number
}

const PALETTE = ['#ffffff']

let particles: Particle[] = []
let raf = 0
let W = 0
let H = 0
const mouse = { x: -99999, y: -99999, tx: -99999, ty: -99999 }

function pickColor() {
  return PALETTE[Math.floor(Math.random() * PALETTE.length)]
}

function sample() {
  const canvas = canvasRef.value
  const wrap = wrapRef.value
  if (!canvas || !wrap)
    return
  const ctx = canvas.getContext('2d')
  if (!ctx)
    return
  W = wrap.clientWidth
  H = wrap.clientHeight
  if (W < 10 || H < 10)
    return
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  canvas.width = Math.floor(W * dpr)
  canvas.height = Math.floor(H * dpr)
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

  const text = 'BETTER AGENT'
  let fontSize = Math.min(W / (text.length * 0.68), H * 0.46)
  fontSize = Math.max(fontSize, 16)
  const font = `800 ${fontSize}px "Segoe UI", system-ui, -apple-system, "Helvetica Neue", sans-serif`

  const off = document.createElement('canvas')
  const octx = off.getContext('2d', { willReadFrequently: true })
  if (!octx)
    return
  octx.font = font
  const tw = Math.ceil(octx.measureText(text).width)
  const th = Math.ceil(fontSize * 1.3)
  const pad = Math.ceil(fontSize * 0.12)
  off.width = tw + pad * 2
  off.height = th + pad * 2
  octx.font = font
  octx.fillStyle = '#fff'
  octx.textBaseline = 'middle'
  octx.textAlign = 'left'
  octx.fillText(text, pad, off.height / 2)

  const img = octx.getImageData(0, 0, off.width, off.height).data
  const step = Math.max(3, Math.round(fontSize / 20))
  const next: Particle[] = []
  const ox = (W - tw) / 2
  const oy = (H - th) / 2 - pad
  for (let y = 0; y < off.height; y += step) {
    for (let x = 0; x < off.width; x += step) {
      if (img[(y * off.width + x) * 4 + 3] > 110) {
        next.push({
          x: ox + x + (Math.random() - 0.5) * step,
          y: oy + y + (Math.random() - 0.5) * step,
          hx: ox + x,
          hy: oy + y,
          vx: 0,
          vy: 0,
          s: Math.random() * 1.3 + 0.7,
          c: pickColor(),
          r: Math.random(),
        })
      }
    }
  }
  particles = next
  fallback.value = particles.length < 5
}

function tick() {
  const canvas = canvasRef.value
  const ctx = canvas?.getContext('2d')
  if (!canvas || !ctx || fallback.value) {
    raf = requestAnimationFrame(tick)
    return
  }
  ctx.clearRect(0, 0, W, H)
  // snappier follow — higher ease factor reduces the perceived latency
  mouse.x += (mouse.tx - mouse.x) * 0.35
  mouse.y += (mouse.ty - mouse.y) * 0.35
  const R = 90
  const R2 = R * R
  for (const p of particles) {
    const dx = p.x - mouse.x
    const dy = p.y - mouse.y
    const d2 = dx * dx + dy * dy
    if (d2 < R2 && d2 > 0.001) {
      const d = Math.sqrt(d2)
      const f = ((R - d) / R) * 5.5
      // organic scatter: rotate the radial push by a per-particle random
      // offset so it's not a perfect circular diffusion
      const a = Math.atan2(dy, dx) + (p.r - 0.5) * 1.7
      p.vx += Math.cos(a) * f
      p.vy += Math.sin(a) * f
    }
    p.vx += (p.hx - p.x) * 0.055
    p.vy += (p.hy - p.y) * 0.055
    p.vx *= 0.88
    p.vy *= 0.88
    p.x += p.vx
    p.y += p.vy
    ctx.globalAlpha = 0.5 + p.r * 0.5
    ctx.fillStyle = p.c
    ctx.beginPath()
    ctx.arc(p.x, p.y, p.s, 0, Math.PI * 2)
    ctx.fill()
  }
  ctx.globalAlpha = 1
  raf = requestAnimationFrame(tick)
}

function onMove(e: MouseEvent) {
  const canvas = canvasRef.value
  if (!canvas)
    return
  const r = canvas.getBoundingClientRect()
  mouse.tx = e.clientX - r.left
  mouse.ty = e.clientY - r.top
}

function onLeave() {
  mouse.tx = -99999
  mouse.ty = -99999
}

let resizeTimer: ReturnType<typeof setTimeout> | undefined
function onResize() {
  clearTimeout(resizeTimer)
  resizeTimer = setTimeout(() => {
    sample()
    resizeWarp()
  }, 120)
}

// ---------------------------------------------------------------------------
// Draggable cube — click & drag to rotate; auto-rotates slowly when idle
// ---------------------------------------------------------------------------
const cubeRef = ref<HTMLElement | null>(null)
let cubeRotX = -22
let cubeRotY = 0
let cubeRaf = 0
let dragging = false
let lastX = 0
let lastY = 0
const cubeStyle = ref({ transform: `rotateX(${cubeRotX}deg) rotateY(${cubeRotY}deg)` })
const innerStyle = ref({ transform: 'rotateX(0deg) rotateY(0deg)' })
let innerRotX = 0
let innerRotY = 0
let innerSpin = 0

function cubeLoop() {
  cubeRaf = requestAnimationFrame(cubeLoop)
  if (!dragging)
    cubeRotY += 0.3
  innerSpin += 0.3

  // inner counter-rotates with the outer (connected feel) + its own slow spin
  innerRotX += (-cubeRotX * 0.8 - innerRotX) * 0.08
  innerRotY += (-cubeRotY * 0.8 + innerSpin - innerRotY) * 0.08

  cubeStyle.value = { transform: `rotateX(${cubeRotX}deg) rotateY(${cubeRotY}deg)` }
  innerStyle.value = { transform: `rotateX(${innerRotX}deg) rotateY(${innerRotY}deg)` }
}

function onCubeDown(e: PointerEvent) {
  dragging = true
  lastX = e.clientX
  lastY = e.clientY
  ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
}

function onCubeMove(e: PointerEvent) {
  if (!dragging)
    return
  const dx = e.clientX - lastX
  const dy = e.clientY - lastY
  cubeRotY += dx * 0.5
  cubeRotX += dy * 0.4
  cubeRotX = Math.max(-80, Math.min(80, cubeRotX))
  lastX = e.clientX
  lastY = e.clientY
}

function onCubeUp() {
  dragging = false
}

// ---------------------------------------------------------------------------
// Space-collapse lens — a soft gravitational warp follows the cursor across
// the whole screen, plus a subtle background parallax for depth.
// ---------------------------------------------------------------------------
const warpRef = ref<HTMLCanvasElement | null>(null)
let warpRaf = 0
let warpW = 0
let warpH = 0
const warp = { x: -99999, y: -99999, tx: -99999, ty: -99999 }
const bgShift = ref({ transform: 'translate(0px, 0px)' })

function resizeWarp() {
  const c = warpRef.value
  if (!c)
    return
  warpW = window.innerWidth
  warpH = window.innerHeight
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  c.width = Math.floor(warpW * dpr)
  c.height = Math.floor(warpH * dpr)
  c.getContext('2d')!.setTransform(dpr, 0, 0, dpr, 0, 0)
}

function warpLoop() {
  warpRaf = requestAnimationFrame(warpLoop)
  const c = warpRef.value
  const ctx = c?.getContext('2d')
  if (!c || !ctx)
    return
  ctx.clearRect(0, 0, warpW, warpH)
  if (warp.tx < -8000)
    return
  warp.x += (warp.tx - warp.x) * 0.08
  warp.y += (warp.ty - warp.y) * 0.08
  const R = 460
  const g = ctx.createRadialGradient(warp.x, warp.y, 0, warp.x, warp.y, R)
  g.addColorStop(0, 'rgba(0, 6, 16, 0.3)')
  g.addColorStop(0.5, 'rgba(8, 20, 32, 0.06)')
  g.addColorStop(0.82, 'rgba(56, 189, 248, 0.1)')
  g.addColorStop(1, 'rgba(56, 189, 248, 0)')
  ctx.fillStyle = g
  ctx.fillRect(0, 0, warpW, warpH)
}

function onWarpMove(e: MouseEvent) {
  warp.tx = e.clientX
  warp.ty = e.clientY
  const nx = e.clientX / window.innerWidth - 0.5
  const ny = e.clientY / window.innerHeight - 0.5
  bgShift.value = { transform: `translate(${nx * -16}px, ${ny * -12}px)` }
}

onMounted(() => {
  sample()
  tick()
  cubeLoop()
  resizeWarp()
  warpLoop()
  window.addEventListener('resize', onResize)
  window.addEventListener('mousemove', onWarpMove)
  document.fonts?.ready?.then(() => sample()).catch(() => {})
})

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  cancelAnimationFrame(cubeRaf)
  cancelAnimationFrame(warpRaf)
  window.removeEventListener('resize', onResize)
  window.removeEventListener('mousemove', onWarpMove)
  clearTimeout(resizeTimer)
})
</script>

<template>
  <main class="login-root">
    <div class="bg-layers" :style="bgShift">
      <div class="starfield" />
      <div class="nebula n1" />
      <div class="nebula n2" />
      <div class="grid-overlay" />
    </div>
    <canvas ref="warpRef" class="warp-canvas" />

    <div class="login-shell">
      <section class="brand">
        <div ref="wrapRef" class="particle-wrap" @mousemove="onMove" @mouseleave="onLeave">
          <canvas ref="canvasRef" class="particle-canvas" />
          <h1 v-if="fallback" class="fallback-title">BETTER AGENT</h1>
        </div>
        <div
          class="cube-stage"
          aria-hidden="true"
          @pointerdown="onCubeDown"
          @pointermove="onCubeMove"
          @pointerup="onCubeUp"
          @pointercancel="onCubeUp"
        >
          <div class="cube-halo" />
          <div ref="cubeRef" class="cube" :style="cubeStyle">
            <div class="face f-front" />
            <div class="face f-back" />
            <div class="face f-right" />
            <div class="face f-left" />
            <div class="face f-top" />
            <div class="face f-bottom" />
          </div>
          <div ref="innerRef" class="cube cube-inner" :style="innerStyle">
            <div class="face f-front" />
            <div class="face f-back" />
            <div class="face f-right" />
            <div class="face f-left" />
            <div class="face f-top" />
            <div class="face f-bottom" />
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-head">
          <div class="tagline-card"><span>虚拟数字人陪伴系统</span></div>
          <h2>登录</h2>
        </div>

        <form class="form" @submit.prevent="submit">
          <label class="field">
            <span>用户</span>
            <input v-model="form.username" type="text" placeholder="请输入用户名" autocomplete="username" />
          </label>
          <label class="field">
            <span>密码</span>
            <input v-model="form.password" type="password" placeholder="请输入密码" autocomplete="current-password" />
          </label>

          <p v-if="errorMsg" class="error" role="alert">{{ errorMsg }}</p>

          <button class="submit" type="submit" :disabled="loading">
            <span v-if="loading" class="spinner" />
            <span>{{ loading ? '请稍候…' : '登 录' }}</span>
          </button>
        </form>
      </section>
    </div>
  </main>
</template>

<route lang="yaml">
meta:
  layout: plain
</route>

<style scoped>
.login-root {
  position: relative;
  min-height: 100vh;
  min-height: 100dvh;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: radial-gradient(1200px 800px at 50% -10%, #0a1a2e 0%, #050b16 55%, #01030a 100%);
  color: #fff;
  font-family: 'Segoe UI', system-ui, -apple-system, 'Helvetica Neue', sans-serif;
}

/* starry sky */
.bg-layers {
  position: absolute;
  inset: 0;
  pointer-events: none;
  will-change: transform;
}

.warp-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 1;
}

.starfield {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    radial-gradient(1px 1px at 20px 30px, rgba(255,255,255,0.9), transparent),
    radial-gradient(1px 1px at 120px 80px, rgba(165,243,252,0.7), transparent),
    radial-gradient(2px 2px at 60px 150px, rgba(255,255,255,0.8), transparent),
    radial-gradient(1px 1px at 170px 40px, rgba(125,211,252,0.7), transparent),
    radial-gradient(1px 1px at 90px 190px, rgba(255,255,255,0.55), transparent),
    radial-gradient(1.5px 1.5px at 30px 120px, rgba(56,189,248,0.7), transparent),
    radial-gradient(1px 1px at 150px 170px, rgba(255,255,255,0.6), transparent),
    radial-gradient(1px 1px at 40px 60px, rgba(165,243,252,0.5), transparent);
  background-size: 200px 200px;
  opacity: 0.9;
}

.nebula {
  position: absolute;
  border-radius: 50%;
  filter: blur(90px);
  pointer-events: none;
}
.n1 { width: 620px; height: 620px; left: -140px; top: -140px; background: #1e3a8a; opacity: 0.34; }
.n2 { width: 560px; height: 560px; right: -140px; bottom: -140px; background: #0e7490; opacity: 0.3; }

.grid-overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(56,189,248,0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(56,189,248,0.05) 1px, transparent 1px);
  background-size: 56px 56px;
  mask-image: radial-gradient(circle at 50% 40%, #000 0%, transparent 78%);
}

.login-shell {
  position: relative;
  z-index: 2;
  display: grid;
  grid-template-columns: 1.2fr 0.8fr;
  gap: 48px;
  align-items: center;
  width: min(1180px, 92vw);
}

.brand {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.particle-wrap {
  position: relative;
  width: 100%;
  height: 170px;
  cursor: crosshair;
}
.particle-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
}
.fallback-title {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: clamp(28px, 6vw, 72px);
  font-weight: 800;
  letter-spacing: 0.04em;
  color: #e0f2fe;
  text-shadow: 0 0 24px rgba(56, 189, 248, 0.5);
}

.tagline-card {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 16px;
}
.tagline-card::before,
.tagline-card::after {
  content: '';
  flex: 0 0 38px;
  height: 1px;
  margin: 0 14px;
  background: linear-gradient(90deg, transparent, rgba(103, 232, 249, 0.8));
}
.tagline-card::after {
  background: linear-gradient(90deg, rgba(103, 232, 249, 0.8), transparent);
}
.tagline-card span {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.28em;
  white-space: nowrap;
  background: linear-gradient(90deg, #67e8f9 0%, #e0f2fe 50%, #67e8f9 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  filter: drop-shadow(0 0 10px rgba(103, 232, 249, 0.45));
}

/* glass cube — two counter-rotating cubes with tech cyan glow */
.cube-stage {
  position: relative;
  width: 220px;
  height: 220px;
  perspective: 900px;
  cursor: grab;
  touch-action: none;
  margin-top: 28px;
}
.cube-stage:active {
  cursor: grabbing;
}

.cube-halo {
  position: absolute;
  inset: -40%;
  background: radial-gradient(circle, rgba(34, 211, 238, 0.3) 0%, rgba(56, 189, 248, 0.12) 42%, transparent 72%);
  filter: blur(18px);
  animation: halo-pulse 4.5s ease-in-out infinite;
  pointer-events: none;
}
.cube {
  position: absolute;
  inset: 0;
  transform-style: preserve-3d;
  will-change: transform;
}
.cube-inner {
  inset: 40px;
}
.face {
  position: absolute;
  inset: 0;
  border: 1px solid rgba(103, 232, 249, 0.72);
  background: linear-gradient(135deg, rgba(34, 211, 238, 0.22), rgba(59, 130, 246, 0.16));
  box-shadow:
    0 0 36px rgba(34, 211, 238, 0.34) inset,
    0 0 26px rgba(59, 130, 246, 0.2);
  backdrop-filter: blur(3px);
}
.cube-inner .face {
  border: 1px solid rgba(255, 255, 255, 0.75);
  background: transparent;
  box-shadow:
    0 0 16px rgba(255, 255, 255, 0.16) inset,
    0 0 14px rgba(255, 255, 255, 0.1);
}
.f-front  { transform: translateZ(110px); }
.f-back   { transform: rotateY(180deg) translateZ(110px); }
.f-right  { transform: rotateY(90deg) translateZ(110px); }
.f-left   { transform: rotateY(-90deg) translateZ(110px); }
.f-top    { transform: rotateX(90deg) translateZ(110px); }
.f-bottom { transform: rotateX(-90deg) translateZ(110px); }

.cube-inner .f-front  { transform: translateZ(68px); }
.cube-inner .f-back   { transform: rotateY(180deg) translateZ(68px); }
.cube-inner .f-right  { transform: rotateY(90deg) translateZ(68px); }
.cube-inner .f-left   { transform: rotateY(-90deg) translateZ(68px); }
.cube-inner .f-top    { transform: rotateX(90deg) translateZ(68px); }
.cube-inner .f-bottom { transform: rotateX(-90deg) translateZ(68px); }

@keyframes cube-spin {
  from { transform: rotateX(-22deg) rotateY(0deg); }
  to   { transform: rotateX(-22deg) rotateY(360deg); }
}

@keyframes halo-pulse {
  0%, 100% { opacity: 0.65; transform: scale(1); }
  50% { opacity: 1; transform: scale(1.09); }
}

.card {
  position: relative;
  padding: 38px 34px;
  border-radius: 22px;
  border: 1px solid rgba(103, 232, 249, 0.22);
  background: linear-gradient(160deg, rgba(8, 20, 32, 0.62), rgba(4, 12, 22, 0.42));
  backdrop-filter: blur(22px);
  box-shadow: 0 30px 80px -30px rgba(0, 0, 0, 0.8), 0 0 40px -20px rgba(34, 211, 238, 0.4);
  color: #fff;
}
.card-head { text-align: center; margin-bottom: 26px; }
.card-head h2 { font-size: 26px; font-weight: 700; margin: 0; color: #fff; }
.card-head p { font-size: 13px; color: rgba(165, 243, 252, 0.72); margin: 8px 0 0; }

.form { display: flex; flex-direction: column; gap: 16px; }
.field { display: flex; flex-direction: column; gap: 7px; }
.field span { font-size: 12px; color: rgba(186, 230, 253, 0.82); letter-spacing: 0.06em; font-weight: 600; }
.field input {
  padding: 13px 14px;
  border-radius: 12px;
  border: 1px solid rgba(103, 232, 249, 0.2);
  background: rgba(8, 20, 32, 0.5);
  color: #fff;
  font-size: 14px;
  outline: none;
  transition: 0.2s;
}
.field input::placeholder { color: rgba(148, 163, 184, 0.6); }
.field input:focus { border-color: rgba(103, 232, 249, 0.75); box-shadow: 0 0 0 3px rgba(34, 211, 238, 0.15); background: rgba(8, 20, 32, 0.7); }

.error { font-size: 13px; color: #fecaca; margin: 0; text-align: center; }

.submit {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 14px 0;
  border: 0;
  border-radius: 12px;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0.16em;
  color: #04121f;
  cursor: pointer;
  background: linear-gradient(120deg, #67e8f9, #22d3ee 55%, #3b82f6);
  background-size: 160% 160%;
  box-shadow: 0 14px 32px -14px rgba(34, 211, 238, 0.75);
  transition: 0.25s;
}
.submit:hover:not(:disabled) { background-position: 100% 0; transform: translateY(-1px); }
.submit:disabled { opacity: 0.7; cursor: default; }

.spinner {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-top-color: #fff;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

@media (max-width: 860px) {
  .login-shell { grid-template-columns: 1fr; gap: 8px; }
  .brand { order: 1; }
  .particle-wrap { height: 100px; }
  .cube-stage { width: 150px; height: 150px; }
  .f-front  { transform: translateZ(75px); }
  .f-back   { transform: rotateY(180deg) translateZ(75px); }
  .f-right  { transform: rotateY(90deg) translateZ(75px); }
  .f-left   { transform: rotateY(-90deg) translateZ(75px); }
  .f-top    { transform: rotateX(90deg) translateZ(75px); }
  .f-bottom { transform: rotateX(-90deg) translateZ(75px); }
  .card { order: 2; }
}
</style>
