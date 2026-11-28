import { useEffect, useMemo, useState } from 'react'
import { navigate } from '../App'
import { api, type ProblemSummary } from '../lib/api'
import { speechSupported } from '../lib/speech'

const DIFFICULTIES = ['easy', 'medium', 'hard'] as const

export default function Home() {
  const [problems, setProblems] = useState<ProblemSummary[] | null>(null)
  const [topic, setTopic] = useState<string | null>(null)
  const [difficulty, setDifficulty] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState<string | null>(null)

  useEffect(() => {
    api.problems().then(setProblems).catch((e) => setError(`Could not reach the backend: ${e.message}`))
  }, [])

  const topics = useMemo(() => [...new Set((problems ?? []).map((p) => p.topic))].sort(), [problems])
  const visible = (problems ?? []).filter(
    (p) => (!topic || p.topic === topic) && (!difficulty || p.difficulty === difficulty),
  )

  async function start(problemId: string) {
    setStarting(problemId)
    try {
      const session = await api.createSession(problemId)
      navigate(`/interview/${session.id}`)
    } catch (e) {
      setError((e as Error).message)
      setStarting(null)
    }
  }

  return (
    <div className="home">
      <header className="home-header">
        <h1>Mock-AI</h1>
        <p>Practise a real coding interview out loud. Pick a topic and difficulty, then talk it through with your AI interviewer.</p>
      </header>

      {!speechSupported.recognition && (
        <div className="notice">Voice needs Google Chrome. In this browser you can still type your answers.</div>
      )}
      {error && <div className="notice error">{error}</div>}

      <section className="filters">
        <div className="filter-row">
          <span className="filter-label">Topic</span>
          <button className={`chip ${!topic ? 'on' : ''}`} onClick={() => setTopic(null)}>All</button>
          {topics.map((t) => (
            <button key={t} className={`chip ${topic === t ? 'on' : ''}`} onClick={() => setTopic(t)}>
              {t.replace(/_/g, ' ')}
            </button>
          ))}
        </div>
        <div className="filter-row">
          <span className="filter-label">Difficulty</span>
          <button className={`chip ${!difficulty ? 'on' : ''}`} onClick={() => setDifficulty(null)}>All</button>
          {DIFFICULTIES.map((d) => (
            <button key={d} className={`chip ${difficulty === d ? 'on' : ''}`} onClick={() => setDifficulty(d)}>
              {d}
            </button>
          ))}
        </div>
      </section>

      <section className="problem-list">
        {problems === null && !error && <p className="muted">Loading problems…</p>}
        {problems !== null && visible.length === 0 && <p className="muted">No problems match these filters yet.</p>}
        {visible.map((p) => (
          <div key={p.id} className="problem-card">
            <div>
              <div className="problem-title">{p.title}</div>
              <div className="problem-meta">
                <span className={`badge ${p.difficulty}`}>{p.difficulty}</span>
                <span className="muted">{p.topic.replace(/_/g, ' ')}</span>
                <span className="muted">C++ · 45 min</span>
              </div>
            </div>
            <button className="primary" disabled={starting !== null} onClick={() => start(p.id)}>
              {starting === p.id ? 'Starting…' : 'Start interview'}
            </button>
          </div>
        ))}
      </section>
    </div>
  )
}
