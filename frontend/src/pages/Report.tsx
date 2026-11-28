import { useEffect, useState } from 'react'
import { navigate } from '../App'
import { api, type ReportData, type SessionData } from '../lib/api'
import { REPORTS_ENABLED } from '../lib/features'

const VERDICTS: Record<ReportData['verdict'], { label: string; cls: string }> = {
  strong_hire: { label: 'Strong hire', cls: 'good' },
  hire: { label: 'Hire', cls: 'good' },
  lean_no_hire: { label: 'Lean no hire', cls: 'warn' },
  no_hire: { label: 'No hire', cls: 'bad' },
}

const AREAS: Record<string, string> = {
  problem_understanding: 'Problem understanding',
  approach: 'Approach',
  complexity: 'Complexity',
  code_quality: 'Code quality',
  testing: 'Testing',
  communication: 'Communication',
}

export default function Report({ sessionId }: { sessionId: string }) {
  const [session, setSession] = useState<SessionData | null>(null)
  const [report, setReport] = useState<ReportData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    api.session(sessionId).then(setSession).catch(() => {})
  }, [sessionId])

  useEffect(() => {
    if (!REPORTS_ENABLED) return
    setError(null)
    api.report(sessionId).then(setReport).catch((e) => setError(e.message))
  }, [sessionId, attempt])

  if (!REPORTS_ENABLED) {
    return (
      <div className="center-page">
        <p>Reports are turned off while we test the conversation.</p>
        <p className="muted">Set VITE_ENABLE_REPORTS=true in frontend/.env.local and restart the frontend to turn them back on.</p>
        <button onClick={() => navigate('/')}>Home</button>
      </div>
    )
  }

  if (error) {
    return (
      <div className="center-page">
        <p className="notice error">{error}</p>
        <div className="row">
          <button onClick={() => setAttempt((a) => a + 1)}>Try again</button>
          <button onClick={() => navigate('/')}>Home</button>
        </div>
      </div>
    )
  }

  if (!report) {
    return (
      <div className="center-page">
        <div className="spinner" />
        <p>Alex is reviewing your interview and tracing your code…</p>
        <p className="muted">This usually takes under a minute.</p>
      </div>
    )
  }

  const v = VERDICTS[report.verdict]
  const cr = report.code_review

  return (
    <div className="report">
      <button className="link" onClick={() => navigate('/')}>← Home</button>
      <header className="report-head">
        <div>
          <div className="muted">Interview report{session ? ` · ${session.problem.title}` : ''}</div>
          <h1>Your result</h1>
        </div>
        <div className={`verdict ${v.cls}`}>{v.label}</div>
      </header>

      <p className="summary">{report.summary}</p>

      <section>
        <h2>Scores</h2>
        <div className="scores">
          {report.scores.map((s) => (
            <div key={s.area} className="score">
              <div className="score-top">
                <span className="score-area">{AREAS[s.area] ?? s.area}</span>
                <span className="score-bar">
                  {[1, 2, 3, 4].map((n) => <i key={n} className={n <= s.score ? `on s${s.score}` : ''} />)}
                </span>
                <span className="score-num">{s.score}/4</span>
              </div>
              <p><b>What happened:</b> {s.evidence}</p>
              <p><b>To improve:</b> {s.how_to_improve}</p>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2>Your code</h2>
        <p>
          <span className={`pill ${cr.is_correct ? 'good' : 'bad'}`}>{cr.is_correct ? 'Correct' : 'Has bugs'}</span>{' '}
          Time {cr.time_complexity} · Space {cr.space_complexity}
        </p>
        <table className="cases">
          <thead>
            <tr><th>Input</th><th>Expected</th><th>Your code gives</th><th /></tr>
          </thead>
          <tbody>
            {cr.traced_cases.map((c, i) => (
              <tr key={i}>
                <td><code>{c.input}</code></td>
                <td><code>{c.expected}</code></td>
                <td><code>{c.candidate_result}</code></td>
                <td>{c.passes ? '✅' : '❌'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {cr.issues.length > 0 && (
          <ul>{cr.issues.map((x) => <li key={x}>{x}</li>)}</ul>
        )}
      </section>

      <div className="two-col">
        <section>
          <h2>Strengths</h2>
          <ul>{report.strengths.map((x) => <li key={x}>{x}</li>)}</ul>
        </section>
        <section>
          <h2>Top things to work on</h2>
          <ul>{report.top_improvements.map((x) => <li key={x}>{x}</li>)}</ul>
        </section>
      </div>

      <section>
        <h2>What a strong candidate would do</h2>
        <p>{report.strong_candidate_would}</p>
      </section>

      {session && session.transcript.length > 0 && (
        <details className="transcript">
          <summary>Full transcript</summary>
          {session.transcript.map((t, i) => (
            <p key={i}>
              <span className="muted">{String(Math.floor(t.elapsed_seconds / 60)).padStart(2, '0')}:{String(t.elapsed_seconds % 60).padStart(2, '0')}</span>{' '}
              <b>{t.kind === 'ai' ? 'Alex' : 'You'}:</b> {t.text}
            </p>
          ))}
        </details>
      )}
    </div>
  )
}
