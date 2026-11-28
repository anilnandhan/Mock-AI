import Editor, { type OnMount } from '@monaco-editor/react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { navigate } from '../App'
import { api, formatClock, streamTurn, type SessionData, type TurnEvent } from '../lib/api'
import { dlog, setDebugSession } from '../lib/debug'
import { REPORTS_ENABLED } from '../lib/features'
import { Listener, Speaker, requestMic, speechSupported } from '../lib/speech'
import { isBargeIn, pauseNeeded } from '../lib/turns'

/**
 * Turn-taking:
 *   ready ──Start──► thinking ──first words──► speaking ──speech ends──► listening
 *   listening ──Send / Ctrl+Enter / silence / time up──► thinking
 *   speaking ──Esc / Interrupt──► listening
 */
type Mode = 'loading' | 'ready' | 'thinking' | 'speaking' | 'listening' | 'ended'

// cut: the candidate kept talking or talked over this reply, so they didn't hear (all of) it.
type ChatMessage = { role: 'ai' | 'candidate'; text: string; cut?: boolean }

const SILENCE_CHECK_MS = 3 * 60 * 1000
const AUTO_SEND_KEY = 'mock-ai:auto-send'

function loadAutoSend() {
  try {
    return localStorage.getItem(AUTO_SEND_KEY) !== 'off'
  } catch {
    return true
  }
}

const PHASE_LABELS: Record<string, string> = {
  intro: 'Intro',
  clarify: 'Clarify',
  approach: 'Approach',
  code: 'Code',
  dry_run: 'Dry run',
  follow_up: 'Follow-up',
  wrap_up: 'Wrap-up',
}

export default function Interview({ sessionId }: { sessionId: string }) {
  const [session, setSession] = useState<SessionData | null>(null)
  const [mode, setMode] = useState<Mode>('loading')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [phase, setPhase] = useState('intro')
  const [heard, setHeard] = useState({ final: '', interim: '' })
  const [typed, setTyped] = useState('')
  const [voiceOn, setVoiceOn] = useState(speechSupported.recognition && speechSupported.synthesis)
  const [error, setError] = useState<string | null>(null)
  // The mic and Alex's voice are separate: a mic problem must not silence Alex.
  const [micLive, setMicLive] = useState(false)
  const [micError, setMicError] = useState<string | null>(null)
  const thinkingSince = useRef(0)
  const [starting, setStarting] = useState(false)
  const [now, setNow] = useState(Date.now())
  // Automatic end of turn: send after a natural pause instead of pressing Send.
  const [autoSend, setAutoSend] = useState(loadAutoSend)
  const [holding, setHolding] = useState(false)
  const [countdown, setCountdown] = useState<number | null>(null)

  const listener = useRef(new Listener()).current
  const speaker = useRef(new Speaker()).current
  const code = useRef('')
  const status = useRef<'active' | 'ended'>('active')
  const streaming = useRef(false)
  const lastActivity = useRef(Date.now())
  const startedAt = useRef(Date.now())
  const timeUpSent = useRef(false)
  const voiceOnRef = useRef(voiceOn)
  const modeRef = useRef(mode)
  const chatEnd = useRef<HTMLDivElement>(null)
  const heardRef = useRef({ final: '', interim: '' })
  const lastSpeechAt = useRef(0)
  const lastTypeAt = useRef(0)
  const phaseRef = useRef(phase)
  const typedRef = useRef(typed)
  const autoSendRef = useRef(autoSend)
  const holdingRef = useRef(holding)
  // The candidate kept talking after their turn was sent, or talked over Alex.
  const supersededRef = useRef(false) // this reply should not be spoken any further
  const interruptedRef = useRef(false) // tell the backend on the next turn
  const replyTextRef = useRef('') // what Alex is saying now, for the echo guard
  const onHeardRef = useRef<(final: string, interim: string) => void>(() => {})

  modeRef.current = mode
  voiceOnRef.current = voiceOn
  phaseRef.current = phase
  typedRef.current = typed
  autoSendRef.current = autoSend
  holdingRef.current = holding
  speaker.enabled = voiceOn

  // Log every turn-taking change.
  useEffect(() => {
    dlog(`mode -> ${mode}`)
  }, [mode])

  // Load the session.
  useEffect(() => {
    setDebugSession(sessionId)
    dlog('interview page opened', {
      recognition: speechSupported.recognition,
      synthesis: speechSupported.synthesis,
      browser: navigator.userAgent,
    })
    api
      .session(sessionId)
      .then((s) => {
        setSession(s)
        setPhase(s.phase)
        code.current = s.code
        status.current = s.status
        startedAt.current = Date.now() - s.elapsed_seconds * 1000
        setMessages(s.transcript.map((t) => ({ role: t.kind, text: t.text })))
        if (s.status === 'ended') setMode('ended')
        else if (s.transcript.length === 0) setMode('ready')
        else setMode('listening')
      })
      .catch((e) => {
        dlog('session load failed', { message: e.message }, 'error')
        setError(e.message)
      })
    return () => {
      listener.stop()
      speaker.interrupt()
      setDebugSession(null)
    }
  }, [sessionId, listener, speaker])

  // Wire up speech callbacks.
  useEffect(() => {
    listener.onChange = (final, interim) => {
      heardRef.current = { final, interim }
      setHeard({ final, interim })
      onHeardRef.current(final, interim)
    }
    listener.onActivity = () => {
      lastActivity.current = Date.now()
      lastSpeechAt.current = Date.now()
      // Speaking again releases a hold.
      if (holdingRef.current) setHolding(false)
    }
    listener.onListeningChange = setMicLive
    listener.onError = (msg) => setMicError(msg)
    speaker.onIdle = () => {
      // The interviewer finished speaking: hand the turn to the candidate.
      if (supersededRef.current) return // the candidate already took the turn back
      if (streaming.current) {
        dlog('voice idle while still streaming; waiting', undefined, 'debug')
        return
      }
      if (status.current === 'ended') {
        setMode('ended')
        return
      }
      setMode('listening')
      lastActivity.current = Date.now()
      // The mic stayed on while Alex spoke; drop anything it heard of his voice.
      if (voiceOnRef.current) {
        listener.stop()
        listener.start()
      }
    }
  }, [listener, speaker])

  useEffect(() => {
    chatEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, heard])

  const sendTurn = useCallback(
    async (event: TurnEvent) => {
      if (streaming.current || status.current === 'ended') {
        dlog('turn not sent', { event, streaming: streaming.current, status: status.current }, 'warn')
        return
      }
      const spoken = listener.stop()
      const text = event === 'message' ? [spoken, typed.trim()].filter(Boolean).join(' ') : ''
      if (event === 'message' && !text) {
        dlog('turn not sent: nothing heard or typed', undefined, 'warn')
        return
      }
      setHolding(false)
      setCountdown(null)
      dlog('turn sent', { event, spokenChars: spoken.length, typedChars: typed.trim().length, text: text.slice(0, 200) })
      const sentAt = performance.now()
      let firstChunk = true
      let replyChars = 0

      const previousMode = modeRef.current
      setTyped('')
      heardRef.current = { final: '', interim: '' }
      setHeard({ final: '', interim: '' })
      setError(null)
      let before: ChatMessage[] = []
      setMessages((m) => {
        before = m
        return [...m, ...(text ? [{ role: 'candidate' as const, text }] : []), { role: 'ai', text: '' }]
      })
      thinkingSince.current = Date.now() // set before render so the wait notice never uses an old time
      setMode('thinking')
      streaming.current = true
      speaker.begin()
      const interrupted = interruptedRef.current
      interruptedRef.current = false
      supersededRef.current = false
      replyTextRef.current = ''
      // Keep listening while Alex thinks and speaks, so the candidate can carry on
      // or cut in. (The echo guard below ignores Alex's own voice.)
      if (voiceOnRef.current && event !== 'end_early' && event !== 'time_up') listener.start()

      try {
        await streamTurn(
          sessionId,
          { event, text, code: code.current, interrupted },
          {
            onText: (chunk) => {
              if (firstChunk) {
                firstChunk = false
                dlog('reply: first words', { afterMs: Math.round(performance.now() - sentAt) })
              }
              replyChars += chunk.length
              replyTextRef.current += chunk
              setMessages((m) => {
                const copy = m.slice()
                const last = copy[copy.length - 1]
                copy[copy.length - 1] = { ...last, text: last.text + chunk }
                return copy
              })
              if (supersededRef.current) return // the candidate is talking; stay quiet
              if (modeRef.current === 'thinking') setMode('speaking')
              speaker.push(chunk)
            },
            onState: (s) => {
              dlog('reply: state', { phase: s.phase, status: s.status })
              setPhase(s.phase)
              status.current = s.status
            },
          },
        )
      } catch (e) {
        // The backend rolled the turn back: restore the screen so the candidate can retry.
        dlog('turn failed', { event, message: (e as Error).message }, 'error')
        streaming.current = false
        speaker.interrupt()
        setError((e as Error).message)
        setMessages(before)
        if (text) setTyped(text)
        setMode(previousMode === 'ready' ? 'ready' : 'listening')
        if (event === 'time_up') timeUpSent.current = false
        return
      }
      dlog('reply: stream done', { chars: replyChars, totalMs: Math.round(performance.now() - sentAt) })
      if (replyChars === 0) dlog('reply had no words', { event }, 'warn')
      streaming.current = false
      if (supersededRef.current) {
        if ((status.current as string) === 'ended') setMode('ended')
        return // the turn is already the candidate's; nothing more to say
      }
      speaker.end() // fires onIdle once speech finishes (immediately if nothing to say)
    },
    [sessionId, typed, listener, speaker],
  )

  const interrupt = useCallback(() => {
    if (modeRef.current !== 'speaking' && modeRef.current !== 'thinking') return
    dlog('candidate interrupted Alex')
    takeTurnBack()
    if (voiceOnRef.current) {
      listener.stop() // drop any of Alex's voice the mic picked up
      listener.start()
    }
  }, [listener, speaker])

  /** The candidate takes the turn back from Alex (kept talking, cut in, or pressed Esc). */
  function takeTurnBack() {
    supersededRef.current = true
    interruptedRef.current = true
    speaker.interrupt()
    setMessages((m) => {
      const copy = m.slice()
      const last = copy[copy.length - 1]
      if (last?.role === 'ai') copy[copy.length - 1] = { ...last, cut: true }
      return copy
    })
    setMode('listening')
    lastActivity.current = Date.now()
    lastSpeechAt.current = Date.now()
  }

  // Speech heard while it is Alex's turn.
  onHeardRef.current = (final, interim) => {
    const m = modeRef.current
    if (m !== 'thinking' && m !== 'speaking') return
    if (supersededRef.current) return
    const heardNow = `${final} ${interim}`.trim()
    if (!heardNow) return
    if (m === 'thinking' && replyTextRef.current === '') {
      // Alex hasn't said anything yet, so this can't be his echo: the candidate
      // simply kept talking after a pause. Their new words continue their answer.
      dlog('candidate kept talking; skipping the reply', { heard: heardNow.slice(0, 120) })
      takeTurnBack()
      return
    }
    const check = isBargeIn(heardNow, replyTextRef.current)
    if (check.yes) {
      dlog('candidate talked over Alex; stopping', { heard: heardNow.slice(0, 120), newWords: check.newWords.slice(0, 12) })
      takeTurnBack()
      // What the mic heard is mixed with Alex's voice: restart, keeping only the
      // candidate's own words so their first words aren't lost.
      listener.stop()
      listener.start()
      listener.seed(check.candidateText)
    } else if (check.newWords.length) {
      dlog('heard during reply, treated as echo', { heard: heardNow.slice(0, 120), newWords: check.newWords }, 'debug')
    }
  }

  const send = useCallback(() => {
    if (modeRef.current === 'listening') void sendTurn('message')
    else dlog('send ignored', { mode: modeRef.current }, 'warn')
  }, [sendTurn])

  // Keep the latest handlers reachable from Monaco and global key listeners.
  const sendRef = useRef(send)
  const interruptRef = useRef(interrupt)
  sendRef.current = send
  interruptRef.current = interrupt

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault()
        sendRef.current()
      } else if (e.key === 'Escape') {
        interruptRef.current()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Automatic end of turn. Every 200 ms while it's the candidate's turn: if they
  // have said something and then gone quiet (no speech, no typing) for long enough
  // given their last words and the phase, send the turn.
  useEffect(() => {
    const id = window.setInterval(() => {
      const h = heardRef.current
      const ready =
        autoSendRef.current &&
        voiceOnRef.current &&
        modeRef.current === 'listening' &&
        !streaming.current &&
        !holdingRef.current &&
        h.final.trim() !== '' &&
        h.interim.trim() === '' && // still mid-phrase
        typedRef.current.trim() === '' // a typed answer is sent by hand
      if (!ready) {
        setCountdown((c) => (c === null ? c : null))
        return
      }
      const { ms, rule } = pauseNeeded(h.final, phaseRef.current)
      const quiet = Date.now() - Math.max(lastSpeechAt.current, lastTypeAt.current)
      const left = ms - quiet
      if (left <= 0) {
        dlog('auto-send: pause detected', { quietMs: quiet, neededMs: ms, rule, lastWords: h.final.trim().slice(-60) })
        setCountdown(null)
        sendRef.current()
        return
      }
      // Show a countdown for the last stretch only, rounded to limit re-renders.
      const shown = quiet >= 700 ? Math.ceil(left / 500) * 500 : null
      setCountdown((c) => (c === shown ? c : shown))
    }, 200)
    return () => window.clearInterval(id)
  }, [])

  function toggleAutoSend() {
    const next = !autoSend
    setAutoSend(next)
    setHolding(false)
    dlog(`auto-send ${next ? 'on' : 'off'}`)
    try {
      localStorage.setItem(AUTO_SEND_KEY, next ? 'on' : 'off')
    } catch {
      /* private mode: the setting just isn't remembered */
    }
  }

  function hold() {
    setHolding(true)
    setCountdown(null)
    dlog('auto-send held by candidate')
  }

  // Clock, silence check-in and time-up.
  useEffect(() => {
    const id = window.setInterval(() => {
      setNow(Date.now())
      if (!session || status.current === 'ended' || streaming.current) return
      if (modeRef.current !== 'listening') return
      const elapsed = (Date.now() - startedAt.current) / 1000
      if (!timeUpSent.current && elapsed >= session.interview_minutes * 60) {
        timeUpSent.current = true
        dlog('time is up; telling Alex')
        void sendTurn('time_up')
        return
      }
      // No check-ins from a tab nobody is looking at; the 3 minutes restart when they return.
      if (document.hidden) {
        lastActivity.current = Date.now()
        return
      }
      if (Date.now() - lastActivity.current > SILENCE_CHECK_MS) {
        lastActivity.current = Date.now()
        dlog('3 minutes of silence; Alex will check in')
        void sendTurn('silence')
      }
    }, 1000)
    return () => window.clearInterval(id)
  }, [session, sendTurn])

  async function startInterview() {
    if (starting) return
    setStarting(true) // the mic prompt makes this async, so guard against a second click
    // Ask for the mic during the click, so the browser prompt appears now
    // rather than silently failing after Alex's intro.
    if (voiceOn && speechSupported.recognition) {
      const problem = await requestMic()
      setMicError(problem)
    }
    await sendTurn('start')
    setStarting(false) // only matters if the start failed and the Start card is shown again
  }

  async function retryMic() {
    setMicError(null)
    const problem = await requestMic()
    if (problem) {
      setMicError(problem)
      return
    }
    if (modeRef.current === 'listening') listener.start()
  }

  function toggleVoice() {
    const next = !voiceOn
    dlog(`voice toggled ${next ? 'on' : 'off'}`)
    setVoiceOn(next)
    if (!next) {
      const spoken = listener.stop()
      if (spoken) setTyped((t) => (t ? `${t} ${spoken}` : spoken))
      speaker.interrupt()
    } else if (modeRef.current === 'listening') {
      void retryMic()
    }
  }

  function endEarly() {
    if (!window.confirm(REPORTS_ENABLED ? 'End the interview now and get your report?' : 'End the interview now?')) return
    speaker.interrupt()
    if (modeRef.current === 'ready') {
      navigate('/')
      return
    }
    void sendTurn('end_early')
  }

  const onEditorMount: OnMount = (editor, monaco) => {
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => sendRef.current())
    editor.addCommand(monaco.KeyCode.Escape, () => interruptRef.current())
  }

  if (error && !session) {
    return (
      <div className="center-page">
        <p className="notice error">{error}</p>
        <button onClick={() => navigate('/')}>Back</button>
      </div>
    )
  }
  if (!session) return <div className="center-page muted">Loading interview…</div>

  const elapsed = Math.min((now - startedAt.current) / 1000, session.interview_minutes * 60)
  const remaining = session.interview_minutes * 60 - elapsed
  const p = session.problem
  const pendingText = [heard.final, heard.interim, typed].filter(Boolean).join(' ').trim()
  const canSend = mode === 'listening' && (heard.final.trim() !== '' || typed.trim() !== '')

  return (
    <div className="interview">
      <header className="topbar">
        <button className="link" onClick={() => navigate('/')}>← Mock-AI</button>
        <div className="title">{p.title}</div>
        <div className="phases">
          {Object.entries(PHASE_LABELS).map(([key, label]) => (
            <span key={key} className={`phase ${key === phase ? 'current' : ''}`}>{label}</span>
          ))}
        </div>
        <div className={`clock ${remaining < 300 ? 'warn' : ''}`}>{formatClock(remaining)}</div>
        {speechSupported.recognition && (
          <button className="ghost" onClick={toggleVoice} title="Turn voice on or off">
            {voiceOn ? '🎙 Voice on' : '⌨️ Text only'}
          </button>
        )}
        {speechSupported.recognition && voiceOn && (
          <button className="ghost" onClick={toggleAutoSend} title="Send your turn automatically when you pause">
            {autoSend ? '⏱ Auto-send on' : '⏱ Auto-send off'}
          </button>
        )}
        {mode !== 'ended' && <button className="danger" onClick={endEarly}>End</button>}
      </header>

      <main className="panes">
        <section className="pane problem">
          <div className="problem-meta">
            <span className={`badge ${p.difficulty}`}>{p.difficulty}</span>
            <span className="muted">{p.topic}</span>
          </div>
          <h2>{p.title}</h2>
          <p className="statement">{p.statement}</p>
          {p.examples.map((ex, i) => (
            <div key={i} className="example">
              <div className="example-label">Example {i + 1}</div>
              <pre>
                <b>Input:</b> {ex.input}
                {'\n'}
                <b>Output:</b> {ex.output}
                {ex.explanation ? `\n${ex.explanation}` : ''}
              </pre>
            </div>
          ))}
          <div className="example-label">Constraints</div>
          <ul className="constraints">
            {p.constraints.map((c) => <li key={c}><code>{c}</code></li>)}
          </ul>
        </section>

        <section className="pane editor">
          <div className="pane-head">
            <span>C++</span>
            <span className="muted">Not compiled — your interviewer reads it</span>
          </div>
          <Editor
            height="100%"
            defaultLanguage="cpp"
            defaultValue={session.code}
            theme="vs-dark"
            onMount={onEditorMount}
            onChange={(v) => {
              code.current = v ?? ''
              lastActivity.current = Date.now()
              lastTypeAt.current = Date.now()
            }}
            options={{
              fontSize: 14,
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              tabSize: 4,
              readOnly: mode === 'ended',
            }}
          />
        </section>

        <section className="pane chat">
          <div className="messages">
            {mode === 'ready' && (
              <div className="start-card">
                <p>Your interviewer, Alex, will introduce the problem and talk with you just like a real interview.</p>
                <ul>
                  <li>Think out loud. Press <kbd>Send</kbd> or <kbd>Ctrl</kbd>+<kbd>Enter</kbd> when you finish speaking.</li>
                  <li>Press <kbd>Esc</kbd> to interrupt Alex.</li>
                  <li>Your code is not run. Explain it and walk through examples.</li>
                  {voiceOn && <li>Chrome will ask for microphone access. Headphones help.</li>}
                </ul>
                <button className="primary big" disabled={starting} onClick={() => void startInterview()}>{starting ? 'Starting…' : 'Start interview'}</button>
              </div>
            )}
            {messages.filter((m) => m.text).map((m, i) => (
              <div key={i} className={`msg ${m.role} ${m.cut ? 'cut' : ''}`}>
                <div className="who">{m.role === 'ai' ? 'Alex' : 'You'}{m.cut ? ' (cut off: you kept talking)' : ''}</div>
                <div className="bubble">{m.text}</div>
              </div>
            ))}
            {mode === 'thinking' && <div className="msg ai"><div className="who">Alex</div><div className="bubble typing">…</div></div>}
            {mode === 'listening' && (pendingText || (voiceOn && micLive)) && (
              <div className="msg candidate draft">
                <div className="who">You {pendingText ? '(not sent yet)' : ''}</div>
                <div className="bubble">
                  {pendingText ? (
                    <>
                      {heard.final} <span className="interim">{heard.interim}</span> {typed}
                    </>
                  ) : (
                    <span className="interim">Listening… start speaking</span>
                  )}
                </div>
              </div>
            )}
            <div ref={chatEnd} />
          </div>

          {error && <div className="notice error small">{error}</div>}

          {mode === 'ended' ? (
            <div className="composer ended">
              <p>The interview is over.</p>
              {REPORTS_ENABLED ? (
                <button className="primary big" onClick={() => navigate(`/report/${sessionId}`)}>View my report</button>
              ) : (
                <>
                  <p className="muted">Reports are turned off while testing (VITE_ENABLE_REPORTS).</p>
                  <button className="primary big" onClick={() => navigate('/')}>New interview</button>
                </>
              )}
            </div>
          ) : mode !== 'ready' && (
            <div className="composer">
              <div className={`status ${mode}`}>
                {mode === 'thinking' &&
                  (Date.now() - thinkingSince.current > 6000
                    ? `Alex is thinking… still waiting after ${Math.round((now - thinkingSince.current) / 1000)}s (the AI service is probably rate-limiting; it retries on its own)`
                    : 'Alex is thinking…')}
                {mode === 'speaking' && (
                  <>
                    Alex is speaking <button className="link" onClick={interrupt}>Interrupt (Esc)</button>
                  </>
                )}
                {mode === 'listening' && !voiceOn && 'Your turn — type your answer'}
                {mode === 'listening' && voiceOn && micError && (
                  <span className="mic-error">
                    🎙 {micError} <button className="link" onClick={() => void retryMic()}>Try mic again</button>
                  </span>
                )}
                {mode === 'listening' && voiceOn && !micError && micLive && countdown !== null && (
                  <span className="auto-send">
                    Sending in {(countdown / 1000).toFixed(1)}s — keep talking to add more{' '}
                    <button className="link" onClick={hold}>Hold</button>
                  </span>
                )}
                {mode === 'listening' && voiceOn && !micError && micLive && countdown === null && (
                  <>
                    <span className="rec-dot" />{' '}
                    {holding
                      ? 'Holding — take your time. Speak again or press Send when ready.'
                      : autoSend
                        ? 'Listening — pause when you finish and it sends itself'
                        : 'Listening — speak, then press Send (Ctrl+Enter)'}
                  </>
                )}
                {mode === 'listening' && voiceOn && !micError && !micLive && (
                  <>
                    Mic is paused <button className="link" onClick={() => void retryMic()}>Resume mic</button>
                  </>
                )}
                {mode === 'loading' && 'Loading…'}
              </div>
              <div className="composer-row">
                <textarea
                  value={typed}
                  placeholder={voiceOn ? 'Or type here…' : 'Type your answer…'}
                  rows={2}
                  onChange={(e) => {
                    setTyped(e.target.value)
                    lastActivity.current = Date.now()
                    lastTypeAt.current = Date.now()
                  }}
                />
                <button className="primary" disabled={!canSend} onClick={send}>Send</button>
              </div>
            </div>
          )}
        </section>
      </main>
    </div>
  )
}
