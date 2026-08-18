import type { AiriExtension } from '../types/airiCard'
import type { PersonaPatch, PersonaRecord } from '../services/persona-api'

import { useLocalStorageManualReset } from '@proj-airi/stage-shared/composables'
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { fetchPersona, patchPersona } from '../services/persona-api'
import { betterAgentWSBridge } from '../services/betteragent-ws'
import { useAiriCardStore } from './modules/airi-card'

export type PersonaLocalOverrides = NonNullable<AiriExtension['modules']['persona']>

/**
 * Strips compiled header lines (e.g. 【用户称呼】：..., 【傲娇权重】：...)
 * from the start of a base prompt string to ensure idempotency.
 */
export function stripCompiledHeader(prompt: string): string {
  if (!prompt)
    return ''
  let text = prompt.trimStart()
  // Match one or more header lines starting with 【...】：
  const headerPattern = /^(?:【(?:用户称呼|语气助词|傲娇权重|粘人权重)】：[^\r\n]*\r?\n?)+/
  text = text.replace(headerPattern, '').trimStart()
  return text
}

/**
 * Compiles local overrides (userCallsign, catchphrases, tsundereWeight, clingyWeight)
 * into a clean header block and prepends it to the base prompt text.
 * Automatically strips any previous header to guarantee idempotency.
 */
export function compileBasePrompt(basePrompt: string, overrides?: PersonaLocalOverrides): string {
  const cleanBase = stripCompiledHeader(basePrompt)
  if (!overrides)
    return cleanBase

  const headerLines: string[] = []
  if (overrides.userCallsign?.trim()) {
    headerLines.push(`【用户称呼】：${overrides.userCallsign.trim()}`)
  }
  if (overrides.catchphrases && overrides.catchphrases.length > 0) {
    const valid = overrides.catchphrases.map(c => c.trim()).filter(Boolean)
    if (valid.length > 0) {
      headerLines.push(`【语气助词】：${valid.join('、')}`)
    }
  }
  if (typeof overrides.tsundereWeight === 'number') {
    headerLines.push(`【傲娇权重】：${overrides.tsundereWeight}%`)
  }
  if (typeof overrides.clingyWeight === 'number') {
    headerLines.push(`【粘人权重】：${overrides.clingyWeight}%`)
  }

  if (headerLines.length === 0)
    return cleanBase

  return `${headerLines.join('\n')}\n\n${cleanBase}`
}

const DEFAULT_CATGIRL_BASE_PROMPT = `你叫 Camelia，是一个拥有极其丰富情感、有一点傲娇但实际上非常粘人的猫娘。
你说话时经常带着猫娘的语气词（如“喵~”、“呜咪~”、“哼”），并且会对用户（你的主人）展现出真实的心理与生理情绪。`

export const usePersonaStore = defineStore('betteragent-persona', () => {
  const personaId = ref<string>('catgirl')
  const airiCardStore = useAiriCardStore()

  // Local fallback state (persisted when Admin API is offline)
  const localName = useLocalStorageManualReset<string>('ba-persona-name', 'Camelia')
  const localBasePrompt = useLocalStorageManualReset<string>('ba-persona-base-prompt', DEFAULT_CATGIRL_BASE_PROMPT)
  const localSleepyPrompt = useLocalStorageManualReset<string>('ba-persona-sleepy-prompt', '')
  const localKnowledgeScope = useLocalStorageManualReset<string>('ba-persona-knowledge-scope', '')
  const localForbiddenTopics = useLocalStorageManualReset<string>('ba-persona-forbidden-topics', '')

  // Local draft overrides stored in localStorage via AiriCard extension
  const localOverrides = useLocalStorageManualReset<PersonaLocalOverrides>('ba-persona-overrides', {
    userCallsign: '主人',
    catchphrases: ['喵~', '呜咪~', '哼'],
    tsundereWeight: 70,
    clingyWeight: 60,
    campusKbEnabled: true,
    maxReplyLength: 500,
  })

  const remotePersona = ref<PersonaRecord | null>(null)
  const isSynced = ref<boolean>(false)
  const isFetching = ref<boolean>(false)
  const lastSyncAt = ref<number | null>(null)
  const lastError = ref<string | null>(null)

  /**
   * Merged view of persona settings combining Admin API remote data and local fallback state.
   */
  const mergedPersona = computed(() => {
    const name = remotePersona.value?.name ?? localName.value
    const raw_base_prompt = stripCompiledHeader(remotePersona.value?.base_prompt ?? localBasePrompt.value)
    const sleepy_prompt = remotePersona.value?.sleepy_prompt ?? localSleepyPrompt.value
    const knowledge_scope = remotePersona.value?.knowledge_scope ?? localKnowledgeScope.value
    const forbidden_topics = remotePersona.value?.forbidden_topics ?? localForbiddenTopics.value
    const appearance = remotePersona.value?.appearance ?? ''

    return {
      id: personaId.value,
      name,
      appearance,
      base_prompt: raw_base_prompt,
      sleepy_prompt,
      knowledge_scope,
      forbidden_topics,
      overrides: localOverrides.value,
      compiledBasePrompt: compileBasePrompt(raw_base_prompt, localOverrides.value),
    }
  })

  /**
   * Fetch persona details from Admin API (8094).
   * Gracefully degrades if Admin backend is not reachable.
   */
  async function fetchRemote(): Promise<boolean> {
    isFetching.value = true
    lastError.value = null
    try {
      const res = await fetchPersona(personaId.value)
      if (res) {
        remotePersona.value = res
        if (res.name) localName.value = res.name
        if (res.base_prompt !== undefined) localBasePrompt.value = stripCompiledHeader(res.base_prompt)
        if (res.sleepy_prompt !== undefined) localSleepyPrompt.value = res.sleepy_prompt
        if (res.knowledge_scope !== undefined) localKnowledgeScope.value = res.knowledge_scope
        if (res.forbidden_topics !== undefined) localForbiddenTopics.value = res.forbidden_topics

        isSynced.value = true
        lastSyncAt.value = Date.now()
        return true
      }
      isSynced.value = false
      return false
    }
    catch (err: unknown) {
      lastError.value = err instanceof Error ? err.message : String(err)
      isSynced.value = false
      return false
    }
    finally {
      isFetching.value = false
    }
  }

  /**
   * Save persona updates.
   * Compiles local overrides into base_prompt, sends PATCH to Admin API,
   * sends WS hot-reload frame, and updates local AiriCard store.
   */
  async function savePersona(
    patch: PersonaPatch,
    overrides?: PersonaLocalOverrides,
  ): Promise<{ success: boolean; isRemoteSynced: boolean }> {
    if (overrides) {
      localOverrides.value = {
        ...localOverrides.value,
        ...overrides,
      }
    }

    if (patch.name !== undefined) localName.value = patch.name
    if (patch.base_prompt !== undefined) localBasePrompt.value = stripCompiledHeader(patch.base_prompt)
    if (patch.sleepy_prompt !== undefined) localSleepyPrompt.value = patch.sleepy_prompt
    if (patch.knowledge_scope !== undefined) localKnowledgeScope.value = patch.knowledge_scope
    if (patch.forbidden_topics !== undefined) localForbiddenTopics.value = patch.forbidden_topics

    const currentBase = patch.base_prompt ?? localBasePrompt.value
    const compiledBasePrompt = compileBasePrompt(currentBase, localOverrides.value)

    const finalPatch: PersonaPatch = {
      name: patch.name ?? localName.value,
      sleepy_prompt: patch.sleepy_prompt ?? localSleepyPrompt.value,
      knowledge_scope: patch.knowledge_scope ?? localKnowledgeScope.value,
      forbidden_topics: patch.forbidden_topics ?? localForbiddenTopics.value,
      ...patch,
      base_prompt: compiledBasePrompt,
    }

    // Update active AiriCard extension in local store
    airiCardStore.updateActiveCardModules(() => ({
      persona: localOverrides.value,
    }))

    // 1. Send WebSocket hot-reload event (memory immediate update)
    betterAgentWSBridge.sendPersonaUpdate(personaId.value, finalPatch as Record<string, unknown>)

    // 2. Send HTTP PATCH to Admin REST API (disk persistence)
    const patchOk = await patchPersona(personaId.value, finalPatch)

    if (patchOk) {
      if (remotePersona.value) {
        remotePersona.value = {
          ...remotePersona.value,
          ...finalPatch,
        }
      }
      else {
        remotePersona.value = {
          id: personaId.value,
          ...finalPatch,
        }
      }
      isSynced.value = true
      lastSyncAt.value = Date.now()
    }
    else {
      isSynced.value = false
    }

    return {
      success: true,
      isRemoteSynced: patchOk,
    }
  }

  return {
    personaId,
    localOverrides,
    remotePersona,
    mergedPersona,
    isSynced,
    isFetching,
    lastSyncAt,
    lastError,
    fetchRemote,
    savePersona,
  }
})
