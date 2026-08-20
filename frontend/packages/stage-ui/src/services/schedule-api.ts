/**
 * Companion Schedule REST API client with graceful degradation fallback.
 * Interacts with http://localhost:8096/api/schedule endpoints.
 */

export interface ScheduleItem {
  schedule_id: string
  chat_id: number
  user_id: number
  title: string
  remind_at: string
  note?: string
  status?: string
}

export interface ScheduleAddPayload {
  chat_id: number
  user_id: number
  title: string
  remind_at: string
  note?: string
}

const COMPANION_API_BASE = (typeof import.meta !== 'undefined' && import.meta.env?.VITE_COMPANION_API_BASE)
  ? import.meta.env.VITE_COMPANION_API_BASE
  : 'http://localhost:8096'

/**
 * Fetch active schedule list for a given chat_id from Companion service.
 * Returns an empty array if Companion backend is unreachable or errors out.
 */
export async function fetchSchedules(chatId: number): Promise<ScheduleItem[]> {
  if (!chatId)
    return []
  try {
    const res = await fetch(`${COMPANION_API_BASE}/api/schedule/list?chat_id=${encodeURIComponent(chatId)}`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
    })
    if (!res.ok)
      return []
    const data = await res.json()
    return (data.schedules || []) as ScheduleItem[]
  }
  catch {
    return []
  }
}

/**
 * Add a new schedule item to Companion service.
 * Returns true on success, false if Companion backend is unreachable or errors out.
 */
export async function addSchedule(payload: ScheduleAddPayload): Promise<boolean> {
  try {
    const res = await fetch(`${COMPANION_API_BASE}/api/schedule/add`, {
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
 * Delete a schedule item by schedule_id.
 * Returns true on success, false if Companion backend is unreachable or errors out.
 */
export async function deleteSchedule(scheduleId: string): Promise<boolean> {
  if (!scheduleId)
    return false
  try {
    const res = await fetch(`${COMPANION_API_BASE}/api/schedule/${encodeURIComponent(scheduleId)}`, {
      method: 'DELETE',
      headers: { Accept: 'application/json' },
    })
    return res.ok
  }
  catch {
    return false
  }
}
