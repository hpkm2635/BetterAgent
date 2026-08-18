<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { useVision } from '../composables/use-vision'

const {
  isVisionActive,
  currentSource,
  statusMessage,
  startVision,
  stopVision,
  toggleVision,
} = useVision()

function handleKeydown(e: KeyboardEvent) {
  // Alt + V to toggle vision privacy shield instantly
  if (e.altKey && (e.key === 'v' || e.key === 'V')) {
    e.preventDefault()
    toggleVision('screen')
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <div class="vision-indicator-container">
    <div
      class="vision-status-card"
      :class="{ 'is-active': isVisionActive }"
    >
      <!-- Indicator Dot -->
      <div class="status-dot-wrapper">
        <span class="status-dot" :class="isVisionActive ? 'dot-active' : 'dot-inactive'" />
        <span v-if="isVisionActive" class="status-pulse" />
      </div>

      <!-- Text Status -->
      <div class="status-info">
        <div class="status-title">
          {{ isVisionActive ? `猫娘观察中 (${currentSource === 'screen' ? '屏幕' : '摄像头'})` : '视觉感知已关闭' }}
        </div>
        <div class="status-sub">
          {{ isVisionActive ? statusMessage : '按 Alt + V 快捷开启/隐藏' }}
        </div>
      </div>

      <!-- Action Buttons -->
      <div class="action-buttons">
        <button
          class="icon-btn"
          :class="{ active: isVisionActive && currentSource === 'screen' }"
          title="切换屏幕观察 (Alt + V)"
          @click="toggleVision('screen')"
        >
          🖥️
        </button>

        <button
          class="icon-btn"
          :class="{ active: isVisionActive && currentSource === 'camera' }"
          title="切换摄像头观察"
          @click="toggleVision('camera')"
        >
          📷
        </button>

        <button
          v-if="isVisionActive"
          class="stop-btn"
          title="一键遮挡 / 保护隐私"
          @click="stopVision()"
        >
          🔒 隐私遮挡
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.vision-indicator-container {
  position: absolute;
  top: 16px;
  left: 16px;
  z-index: 99;
  font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  user-select: none;
}

.vision-status-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  background: rgba(18, 18, 24, 0.65);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 999px;
  color: #f1f5f9;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.vision-status-card.is-active {
  border-color: rgba(34, 197, 94, 0.4);
  background: rgba(18, 30, 24, 0.85);
  box-shadow: 0 8px 24px rgba(34, 197, 94, 0.15);
}

.status-dot-wrapper {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 12px;
  height: 12px;
}

.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  transition: background-color 0.3s ease;
}

.dot-active {
  background-color: #22c55e;
}

.dot-inactive {
  background-color: #64748b;
}

.status-pulse {
  position: absolute;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background-color: rgba(34, 197, 94, 0.5);
  animation: pulse-ring 1.8s cubic-bezier(0.215, 0.61, 0.355, 1) infinite;
}

@keyframes pulse-ring {
  0% {
    transform: scale(0.8);
    opacity: 0.8;
  }
  80%, 100% {
    transform: scale(2.2);
    opacity: 0;
  }
}

.status-info {
  display: flex;
  flex-direction: column;
}

.status-title {
  font-size: 12px;
  font-weight: 600;
  line-height: 1.2;
}

.status-sub {
  font-size: 10px;
  color: #94a3b8;
  line-height: 1.2;
}

.action-buttons {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: 6px;
}

.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(255, 255, 255, 0.05);
  cursor: pointer;
  font-size: 13px;
  transition: all 0.2s ease;
}

.icon-btn:hover {
  background: rgba(255, 255, 255, 0.2);
  transform: scale(1.05);
}

.icon-btn.active {
  background: rgba(34, 197, 94, 0.25);
  border-color: #22c55e;
}

.stop-btn {
  padding: 4px 10px;
  font-size: 11px;
  font-weight: 500;
  color: #f87171;
  background: rgba(239, 68, 68, 0.15);
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.stop-btn:hover {
  background: rgba(239, 68, 68, 0.3);
  color: #ffffff;
}
</style>
