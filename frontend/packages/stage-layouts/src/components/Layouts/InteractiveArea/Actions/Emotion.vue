<script setup lang="ts">
import { ref } from 'vue'
import EmotionHUDWidget from '../../../../../../../apps/stage-web/src/components/EmotionHUDWidget.vue'

const show = ref(false)
</script>

<template>
  <button
    title="Catgirl Emotion Status"
    :class="[
      'w-fit p-2',
      'flex justify-center md:items-center self-end',
      'border-2 border-solid border-neutral-100/60 dark:border-neutral-800/30',
      'bg-neutral-50/70 dark:bg-neutral-800/70',
      'backdrop-blur-md',
      'rounded-xl transition-transform active:scale-95',
    ]"
    @click="show = !show"
  >
    <div class="i-solar:heart-pulse-bold-duotone size-5 text-rose-500 dark:text-rose-400" />
  </button>
  <EmotionHUDWidget v-model="show" />
</template>
