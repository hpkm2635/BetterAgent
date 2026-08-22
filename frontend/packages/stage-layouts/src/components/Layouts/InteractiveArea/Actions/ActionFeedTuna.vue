<script setup lang="ts">
import { betterAgentWSBridge } from '@proj-airi/stage-ui/services/betteragent-ws'
import { ref } from 'vue'

const isDragging = ref(false)
const pointerX = ref(0)
const pointerY = ref(0)
const initialX = ref(0)
const initialY = ref(0)
const isSnappingBack = ref(false)
const showFloatingToast = ref(false)
const toastX = ref(0)
const toastY = ref(0)

function onPointerDown(e: PointerEvent) {
  isDragging.value = true
  isSnappingBack.value = false
  initialX.value = e.clientX
  initialY.value = e.clientY
  pointerX.value = e.clientX
  pointerY.value = e.clientY

  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', onPointerUp)
}

function onPointerMove(e: PointerEvent) {
  if (!isDragging.value)
    return
  pointerX.value = e.clientX
  pointerY.value = e.clientY
}

function onPointerUp(_e: PointerEvent) {
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)

  if (!isDragging.value)
    return

  const deltaX = pointerX.value - initialX.value
  const deltaY = pointerY.value - initialY.value
  const dist = Math.hypot(deltaX, deltaY)

  // If dragged more than 60px away, treat as successful drop feed!
  if (dist > 60) {
    triggerFeed(pointerX.value, pointerY.value)
    isDragging.value = false
  }
  else {
    // Snap back smoothly to origin button
    isSnappingBack.value = true
    setTimeout(() => {
      isDragging.value = false
      isSnappingBack.value = false
    }, 300)
  }
}

function triggerFeed(x: number, y: number) {
  toastX.value = x || window.innerWidth / 2
  toastY.value = y || window.innerHeight / 2
  showFloatingToast.value = true

  // Send feed text action to BetterAgent WebGateway
  betterAgentWSBridge.sendUserText('[喂食金枪鱼] 给了 Camelia 一罐美味的金枪鱼罐头！喵~')

  setTimeout(() => {
    showFloatingToast.value = false
  }, 2000)
}
</script>

<template>
  <div class="relative inline-block">
    <button
      title="拖拽金枪鱼喂食 (Drag to Feed)"
      :class="[
        'w-fit p-2 select-none touch-none',
        'flex justify-center md:items-center self-end',
        'border-2 border-solid border-neutral-100/60 dark:border-neutral-800/30',
        'bg-neutral-50/70 dark:bg-neutral-800/70',
        'backdrop-blur-md',
        'rounded-xl transition-transform active:scale-95 cursor-grab active:cursor-grabbing',
      ]"
      @pointerdown="onPointerDown"
      @click="() => triggerFeed(initialX, initialY)"
    >
      <div class="i-solar:cup-hot-bold size-5 text-amber-500 dark:text-amber-400" />
    </button>

    <!-- Floating Draggable Tuna Icon Overlay -->
    <Teleport to="body">
      <div
        v-if="isDragging"
        class="pointer-events-none fixed z-[99999] flex flex-col items-center gap-1 -translate-x-1/2 -translate-y-1/2"
        :class="{ 'transition-all duration-300 ease-out': isSnappingBack }"
        :style="{
          left: isSnappingBack ? `${initialX}px` : `${pointerX}px`,
          top: isSnappingBack ? `${initialY}px` : `${pointerY}px`,
        }"
      >
        <div class="h-14 w-14 border-2 border-amber-400 rounded-2xl bg-amber-500/30 p-1.5 shadow-2xl backdrop-blur-md animate-bounce">
          <img src="../../../../../../../apps/stage-web/src/assets/tuna_treat_icon.png" class="h-full w-full object-contain" alt="Tuna Treat">
        </div>
        <span class="rounded-full border border-amber-400/40 bg-black/80 px-2 py-0.5 text-[10px] font-bold text-amber-300 shadow-md">
          松开投喂喵~ 🐟
        </span>
      </div>

      <!-- Feed Floating Hearts & Value Toast -->
      <div
        v-if="showFloatingToast"
        class="pointer-events-none fixed z-[99999] flex flex-col items-center gap-1 -translate-x-1/2 -translate-y-1/2 animate-bounce"
        :style="{ left: `${toastX}px`, top: `${toastY - 35}px` }"
      >
        <div class="flex items-center gap-1.5 border border-amber-500/60 rounded-full bg-amber-950/90 px-3 py-1.5 text-xs font-bold text-amber-300 shadow-2xl backdrop-blur-md">
          <div class="i-solar:heart-bold animate-ping text-rose-500" />
          <span>饱腹度 +35% | 好感度 +1.0</span>
        </div>
      </div>
    </Teleport>
  </div>
</template>
