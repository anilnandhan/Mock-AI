/**
 * Debug log for the interview screen. Each entry goes to the browser console
 * (prefixed "[mock-ai]") and is sent to the backend in small batches, so it lands
 * in backend/logs/mock_ai.log next to the server's own log for the same turn.
 */

type Level = 'debug' | 'info' | 'warn' | 'error'
type Entry = { t: string; level: Level; msg: string; data?: Record<string, unknown> }

let sessionId: string | null = null
let queue: Entry[] = []
let timer: number | null = null

export function setDebugSession(id: string | null) {
  flush()
  sessionId = id
}

function clock() {
  const d = new Date()
  return d.toTimeString().slice(0, 8) + '.' + String(d.getMilliseconds()).padStart(3, '0')
}

export function dlog(msg: string, data?: Record<string, unknown>, level: Level = 'info') {
  const entry: Entry = { t: clock(), level, msg, ...(data ? { data } : {}) }
  const out = level === 'error' ? console.error : level === 'warn' ? console.warn : console.log
  out(`[mock-ai] ${entry.t} ${msg}`, data ?? '')
  queue.push(entry)
  // Errors go out at once; everything else is batched.
  if (level === 'error' || level === 'warn') flush()
  else if (timer === null) timer = window.setTimeout(flush, 1500)
}

export function flush() {
  if (timer !== null) window.clearTimeout(timer)
  timer = null
  if (!queue.length) return
  const body = JSON.stringify({ session_id: sessionId, entries: queue.splice(0, 200) })
  // keepalive lets the last batch survive a page close.
  fetch('/api/client-log', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    keepalive: true,
  }).catch(() => {
    /* logging must never break the interview */
  })
}

if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', flush)
  window.addEventListener('error', (e) => dlog('page error', { message: e.message }, 'error'))
  window.addEventListener('unhandledrejection', (e) => dlog('unhandled promise rejection', { reason: String(e.reason) }, 'error'))
}
