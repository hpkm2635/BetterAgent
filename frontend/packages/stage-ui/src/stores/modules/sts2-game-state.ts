import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export const useSTS2GameStateStore = defineStore('sts2-game-state', () => {
  const floor = ref(1)
  const hp = ref(0)
  const maxHp = ref(0)
  const gold = ref(0)
  const act = ref(1)
  const lastUpdatedAt = ref<number | null>(null)

  const isLikelyActive = computed(() => {
    if (!lastUpdatedAt.value)
      return false
    return Date.now() - lastUpdatedAt.value < 25000
  })

  function updateState(data: { floor: number, hp: number, max_hp: number, gold: number, act: number }) {
    floor.value = data.floor
    hp.value = data.hp
    maxHp.value = data.max_hp
    gold.value = data.gold
    act.value = data.act
    lastUpdatedAt.value = Date.now()
  }

  return {
    floor,
    hp,
    maxHp,
    gold,
    act,
    lastUpdatedAt,
    isLikelyActive,
    updateState,
  }
})
