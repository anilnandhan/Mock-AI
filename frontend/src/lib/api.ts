export type ProblemSummary = {
  id: string
  title: string
  topic: string
  difficulty: string
}

export type Problem = ProblemSummary & {
  statement: string
  examples: { input: string; output: string; explanation?: string | null }[]
  constraints: string[]
  starter_code: string
}

export type TranscriptEntry = {
  kind: 'ai' | 'candidate'
  text: string
  elapsed_seconds: number
  phase: string
}

export type SessionData = {
  id: string
  problem: Problem
  phase: string
  status: 'active' | 'ended'
  hint_level_max: number
  elapsed_seconds: number
  interview_minutes: number
  transcript: TranscriptEntry[]
  code: string
  has_report: boolean
}

export type TurnEvent = 'start' | 'message' | 'silence' | 'time_up' | 'end_early'

export type TurnState = {
  phase: string
  hint_level_max: number
  status: 'active' | 'ended'
}

export type ScoreArea =
  | 'problem_understanding'
  | 'approach'
  | 'complexity'
  | 'code_quality'
  | 'testing'
  | 'communication'

export type ReportData = {
  verdict: 'strong_hire' | 'hire' | 'lean_no_hire' | 'no_hire'
  summary: string
  scores: { area: ScoreArea; score: 1 | 2 | 3 | 4; evidence: string; how_to_improve: string }[]
  code_review: {
    is_correct: boolean
    time_complexity: string
    space_complexity: string
    traced_cases: { input: string; expected: string; candidate_result: string; passes: boolean }[]
    issues: string[]
  }
  strengths: string[]
  top_improvements: string[]
  strong_candidate_would: string
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) detail = String(body.detail)
    } catch {
      /* not JSON */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  problems: () => fetch('/api/problems').then((r) => json<ProblemSummary[]>(r)),

  createSession: (problemId: string) =>
    fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ problem_id: problemId }),
    }).then((r) => json<SessionData>(r)),

  session: (id: string) => fetch(`/api/sessions/${id}`).then((r) => json<SessionData>(r)),

  report: (id: string) =>
    fetch(`/api/sessions/${id}/report`, { method: 'POST' }).then((r) => json<ReportData>(r)),
}

type TurnHandlers = {
  onText: (chunk: string) => void
  onState: (state: TurnState) => void
}

/** Sends one turn and reads the streamed reply (server-sent events over a POST). */
export async function streamTurn(
  sessionId: string,
  body: { event: TurnEvent; text?: string; code?: string; interrupted?: boolean },
  handlers: TurnHandlers,
): Promise<void> {
  const res = await fetch(`/api/sessions/${sessionId}/turn`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok || !res.body) {
    await json(res)
    throw new Error('No response stream')
  }

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const raw = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      if (!raw.startsWith('data: ')) continue
      const msg = JSON.parse(raw.slice(6))
      if (msg.type === 'text') handlers.onText(msg.text)
      else if (msg.type === 'state') handlers.onState(msg)
      else if (msg.type === 'error') throw new Error(msg.message)
    }
  }
}

export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}
