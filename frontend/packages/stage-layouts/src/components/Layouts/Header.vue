<script setup lang="ts">
import { ProfileSwitcherPopover } from '@proj-airi/stage-ui/components'
import { useRouter } from 'vue-router'

import HeaderAvatar from './HeaderAvatar.vue'
import HeaderLink from './HeaderLink.vue'
import ActionAbout from './InteractiveArea/Actions/About.vue'
import ActionEmotion from './InteractiveArea/Actions/Emotion.vue'
import ActionFeedTuna from './InteractiveArea/Actions/ActionFeedTuna.vue'
import ActionSchedule from './InteractiveArea/Actions/Schedule.vue'

const router = useRouter()

function handleNavigation() {
  router.push('/settings/airi-card')
}
</script>

<template>
  <header mb-1 w-full flex items-center justify-between gap-2>
    <HeaderLink />
    <div flex items-center gap-2>
      <RouterLink
        to="/history"
        :class="[
          'h-8 w-8 flex items-center justify-center rounded-md outline-none',
          'text-lg text-neutral-500 transition-all duration-200 active:scale-95',
          'hover:bg-primary-100/60 hover:text-primary-600 dark:text-neutral-400 dark:hover:bg-primary-900/40 dark:hover:text-primary-300',
        ]"
        title="历史会话"
      >
        <div class="i-solar:history-bold-duotone h-5 w-5" />
      </RouterLink>
      <RouterLink
        to="/settings/schedule"
        :class="[
          'h-8 w-8 flex items-center justify-center rounded-md outline-none',
          'text-lg text-neutral-500 transition-all duration-200 active:scale-95',
          'hover:bg-primary-100/60 hover:text-primary-600 dark:text-neutral-400 dark:hover:bg-primary-900/40 dark:hover:text-primary-300',
        ]"
        title="日程提醒"
      >
        <div class="i-solar:calendar-mark-bold-duotone h-5 w-5" />
      </RouterLink>
      <RouterLink
        to="/settings/memory"
        :class="[
          'h-8 w-8 flex items-center justify-center rounded-md outline-none',
          'text-lg text-neutral-500 transition-all duration-200 active:scale-95',
          'hover:bg-primary-100/60 hover:text-primary-600 dark:text-neutral-400 dark:hover:bg-primary-900/40 dark:hover:text-primary-300',
        ]"
        title="记忆管理"
      >
        <div class="i-solar:leaf-bold-duotone h-5 w-5" />
      </RouterLink>
      <ActionSchedule />
      <ActionEmotion />
      <ActionFeedTuna />
      <ActionAbout />
      <ProfileSwitcherPopover @manage="handleNavigation" />
      <HeaderAvatar />
    </div>
  </header>
</template>
