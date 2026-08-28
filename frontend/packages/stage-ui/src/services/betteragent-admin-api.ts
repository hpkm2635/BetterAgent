export interface ScheduleRecord {
  schedule_id: string
  title: string
  remind_at: string
  note: string
  status: string
}

export interface ScheduleCreatePayload {
  chat_id: number
  user_id: number
  title: string
  remind_at: string
  note?: string
}

export interface SessionSummary {
  chat_id: number
  message_count: number
  last_timestamp: number | null
  preview: string
}

export interface SessionMessage {
  message_id: string | number
  role: string
  content: string
  timestamp: number
}

export interface LongTermMemory {
  id: string
  text: string
  timestamp?: number
  metadata?: Record<string, unknown>
}

export interface MemoryProfile {
  user_id: number
  display_name: string
  known_facts: string[]
  dislikes: string[]
  last_seen?: string | null
}

export interface MemoryProfilePatch {
  display_name?: string
  known_facts?: string[]
  dislikes?: string[]
}

const ADMIN_API_BASE = (typeof import.meta !== 'undefined' && import.meta.env?.VITE_ADMIN_API_BASE)
  ? import.meta.env.VITE_ADMIN_API_BASE
  : 'http://localhost:8094'

export function resolveBetterAgentWebId(chatId?: number | null): number {
  const envUserId = (typeof import.meta !== 'undefined' && import.meta.env?.VITE_BETTERAGENT_USER_ID)
    ? import.meta.env.VITE_BETTERAGENT_USER_ID
    : undefined
  if (envUserId && !Number.isNaN(Number(envUserId)))
    return Number(envUserId)
  return chatId ?? 0
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T | null> {
  try {
    const headers = new Headers(init?.headers)
    headers.set('Accept', 'application/json')
    const adminToken = (typeof import.meta !== 'undefined' && import.meta.env?.VITE_ADMIN_API_TOKEN)
      ? import.meta.env.VITE_ADMIN_API_TOKEN
      : undefined
    if (adminToken)
      headers.set('Authorization', `Bearer ${adminToken}`)
    if (init?.body)
      headers.set('Content-Type', 'application/json')
    const res = await fetch(`${ADMIN_API_BASE}${path}`, { ...init, headers })
    if (!res.ok)
      return null
    return (await res.json()) as T
  }
  catch {
    return null
  }
}

export async function listSchedules(chatId: number): Promise<ScheduleRecord[]> {
  const payload = await requestJson<{ schedules: ScheduleRecord[] }>(
    `/api/admin/schedules?chat_id=${encodeURIComponent(chatId)}`,
  )
  return payload?.schedules ?? []
}

export async function createSchedule(input: ScheduleCreatePayload): Promise<boolean> {
  const res = await requestJson<{ schedule_id: string }>('/api/admin/schedules', {
    method: 'POST',
    body: JSON.stringify(input),
  })
  return !!res?.schedule_id
}

export async function deleteSchedule(scheduleId: string): Promise<boolean> {
  const res = await requestJson<{ status: string }>(
    `/api/admin/schedules/${encodeURIComponent(scheduleId)}`,
    { method: 'DELETE' },
  )
  return res?.status === 'deleted'
}

export async function getSessionOverview(): Promise<SessionSummary[]> {
  const payload = await requestJson<{ sessions: SessionSummary[] }>('/api/admin/sessions/overview')
  return payload?.sessions ?? []
}

export async function getSessionMessages(chatId: number): Promise<SessionMessage[]> {
  const payload = await requestJson<{ sessions: SessionMessage[] }>(
    `/api/admin/sessions?chat_id=${encodeURIComponent(chatId)}`,
  )
  return payload?.sessions ?? []
}

export async function getShortTermMemory(userId: number): Promise<SessionMessage[]> {
  const payload = await requestJson<{ messages: SessionMessage[] }>(
    `/api/admin/memory/short-term?user_id=${encodeURIComponent(userId)}`,
  )
  return payload?.messages ?? []
}

export async function clearShortTermMemory(userId: number): Promise<boolean> {
  const res = await requestJson<{ status: string }>(
    `/api/admin/memory/short-term?user_id=${encodeURIComponent(userId)}`,
    { method: 'DELETE' },
  )
  return res?.status === 'cleared'
}

export async function getLongTermMemory(userId: number, query?: string): Promise<LongTermMemory[]> {
  const params = new URLSearchParams({ user_id: String(userId) })
  if (query?.trim())
    params.set('query', query.trim())
  const payload = await requestJson<{ memories: LongTermMemory[] }>(
    `/api/admin/memory/long-term?${params.toString()}`,
  )
  return payload?.memories ?? []
}

export async function deleteLongTermMemory(pointId: string): Promise<boolean> {
  const res = await requestJson<{ status: string }>(
    `/api/admin/memory/long-term/${encodeURIComponent(pointId)}`,
    { method: 'DELETE' },
  )
  return res?.status === 'deleted'
}

export async function getMemoryProfile(userId: number): Promise<MemoryProfile | null> {
  return requestJson<MemoryProfile>(`/api/admin/memory/profile?user_id=${encodeURIComponent(userId)}`)
}

export async function updateMemoryProfile(userId: number, patch: MemoryProfilePatch): Promise<MemoryProfile | null> {
  return requestJson<MemoryProfile>(
    `/api/admin/memory/profile/${encodeURIComponent(userId)}`,
    { method: 'PUT', body: JSON.stringify(patch) },
  )
}
