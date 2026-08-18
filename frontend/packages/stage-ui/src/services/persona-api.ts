/**
 * Persona Admin REST API client with graceful degradation fallback.
 * Interacts with http://localhost:8094/api/admin/personas endpoints.
 */

export interface PersonaPatch {
  name?: string
  appearance?: string
  base_prompt?: string
  sleepy_prompt?: string
  knowledge_scope?: string
  forbidden_topics?: string
}

export interface PersonaRecord extends PersonaPatch {
  id: string
  tts_provider?: string
  voice_id?: string
}

const ADMIN_API_BASE = (typeof import.meta !== 'undefined' && import.meta.env?.VITE_ADMIN_API_BASE)
  ? import.meta.env.VITE_ADMIN_API_BASE
  : 'http://localhost:8094'

/**
 * Fetch persona details from Admin API.
 * Returns null if Admin backend is unreachable or returns an error.
 */
export async function fetchPersona(personaId: string): Promise<PersonaRecord | null> {
  try {
    const res = await fetch(`${ADMIN_API_BASE}/api/admin/personas/${encodeURIComponent(personaId)}`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
    })
    if (!res.ok)
      return null
    return (await res.json()) as PersonaRecord
  }
  catch {
    return null
  }
}

/**
 * Send whitelist PATCH update to Admin API.
 * Returns true on success, false if Admin backend is unreachable or errors out.
 */
export async function patchPersona(personaId: string, patch: PersonaPatch): Promise<boolean> {
  try {
    const res = await fetch(`${ADMIN_API_BASE}/api/admin/personas/${encodeURIComponent(personaId)}`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      body: JSON.stringify(patch),
    })
    return res.ok
  }
  catch {
    return false
  }
}
