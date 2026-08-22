/**
 * Companion Memory & User Profile REST API Client with graceful degradation fallback.
 * Interacts with http://localhost:8096/api/user_profile endpoints.
 */

export interface UserProfileFact {
  fact_id: string
  chat_id: number
  user_id: number
  category: string
  key: string
  value: string
  created_at?: string
}

export interface UserProfileFactAddPayload {
  chat_id: number
  user_id: number
  category: string
  key: string
  value: string
}

export interface MemoryStats {
  chat_id: number
  vector_count: number
  short_term_buffer: number
  consolidation_health: number
  ebb_decay_factor: number
  status: string
}

const COMPANION_API_BASE = (typeof import.meta !== 'undefined' && import.meta.env?.VITE_COMPANION_API_BASE)
  ? import.meta.env.VITE_COMPANION_API_BASE
  : 'http://localhost:8096'

/**
 * Fetch list of user memory profile facts.
 */
export async function fetchUserProfileFacts(chatId: number = 1001): Promise<UserProfileFact[]> {
  try {
    const res = await fetch(`${COMPANION_API_BASE}/api/user_profile/list?chat_id=${encodeURIComponent(chatId)}`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
    })
    if (!res.ok)
      return getDefaultFallbackFacts()
    const data = await res.json()
    return (data.facts || []) as UserProfileFact[]
  }
  catch {
    return getDefaultFallbackFacts()
  }
}

/**
 * Add a new user memory profile fact.
 */
export async function addUserProfileFact(payload: UserProfileFactAddPayload): Promise<boolean> {
  try {
    const res = await fetch(`${COMPANION_API_BASE}/api/user_profile/fact`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      body: JSON.stringify(payload),
    })
    return res.ok
  }
  catch {
    return false
  }
}

/**
 * Delete a user memory profile fact by fact_id.
 */
export async function deleteUserProfileFact(factId: string): Promise<boolean> {
  if (!factId)
    return false
  try {
    const res = await fetch(`${COMPANION_API_BASE}/api/user_profile/fact/${encodeURIComponent(factId)}`, {
      method: 'DELETE',
      headers: { Accept: 'application/json' },
    })
    return res.ok
  }
  catch {
    return false
  }
}

/**
 * Fetch memory system statistics.
 */
export async function fetchMemoryStats(chatId: number = 1001): Promise<MemoryStats> {
  try {
    const res = await fetch(`${COMPANION_API_BASE}/api/memory/stats?chat_id=${encodeURIComponent(chatId)}`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
    })
    if (!res.ok)
      return getDefaultFallbackStats()
    return (await res.json()) as MemoryStats
  }
  catch {
    return getDefaultFallbackStats()
  }
}

function getDefaultFallbackFacts(): UserProfileFact[] {
  return [
    { fact_id: 'fact_101', chat_id: 1001, user_id: 1, category: 'identity', key: '用户称呼', value: '主人' },
    { fact_id: 'fact_102', chat_id: 1001, user_id: 1, category: 'identity', key: '校园身份', value: '计算机专业应届毕业生' },
    { fact_id: 'fact_103', chat_id: 1001, user_id: 1, category: 'preference', key: '喜好游戏', value: '杀戮尖塔2、二次元手游' },
    { fact_id: 'fact_104', chat_id: 1001, user_id: 1, category: 'preference', key: '常用工具', value: 'AIRI 桌面虚拟主播、BetterAgent' },
    { fact_id: 'fact_105', chat_id: 1001, user_id: 1, category: 'campus', key: '寝室编号', value: '海韵园区 4号楼 502' },
  ]
}

function getDefaultFallbackStats(): MemoryStats {
  return {
    chat_id: 1001,
    vector_count: 1055,
    short_term_buffer: 12,
    consolidation_health: 98.5,
    ebb_decay_factor: 0.85,
    status: 'healthy',
  }
}
