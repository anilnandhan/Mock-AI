/**
 * Browser speech for the POC (Chrome):
 * - Listener: speech-to-text with the Web Speech API.
 * - Speaker: text-to-speech that speaks each sentence as soon as it is complete,
 *   so the interviewer starts talking before the whole reply has arrived.
 */

import { dlog } from './debug'

// Minimal typings: the Web Speech recognition API is not in TypeScript's DOM lib.
type RecognitionResult = { isFinal: boolean; 0: { transcript: string } }
type RecognitionEvent = { resultIndex: number; results: ArrayLike<RecognitionResult> }
type Recognition = {
  continuous: boolean
  interimResults: boolean
  lang: string
  onresult: ((e: RecognitionEvent) => void) | null
  onend: (() => void) | null
  onerror: ((e: { error: string }) => void) | null
  start: () => void
  stop: () => void
  abort: () => void
}
type RecognitionCtor = new () => Recognition

function recognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

export const speechSupported = {
  recognition: recognitionCtor() !== null,
  synthesis: typeof window !== 'undefined' && 'speechSynthesis' in window,
}

export class Listener {
  private rec: Recognition | null = null
  private active = false
  private finalText = ''
  private restarts: number[] = []

  onChange: (finalText: string, interim: string) => void = () => {}
  onActivity: () => void = () => {}
  /** Fired when the mic cannot be used. Listening has stopped; speech output is unaffected. */
  onError: (message: string) => void = () => {}
  onListeningChange: (listening: boolean) => void = () => {}

  start() {
    const Ctor = recognitionCtor()
    if (!Ctor || this.active) return
    this.active = true
    this.finalText = ''
    this.restarts = []
    dlog('mic: start listening')
    this.onChange('', '')
    this.onListeningChange(true)
    this.begin(Ctor)
  }

  private fail(message: string) {
    dlog('mic: FAILED, listening stopped', { message }, 'error')
    this.active = false
    this.rec?.abort()
    this.rec = null
    this.onListeningChange(false)
    this.onError(message)
  }

  private begin(Ctor: RecognitionCtor) {
    const rec = new Ctor()
    rec.continuous = true
    rec.interimResults = true
    rec.lang = 'en-US'
    rec.onresult = (e) => {
      this.restarts = []
      let interim = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i]
        if (r.isFinal) {
          this.finalText += r[0].transcript.trim() + ' '
          dlog('mic: heard', { text: r[0].transcript.trim() })
        }
        else interim += r[0].transcript
      }
      this.onActivity()
      this.onChange(this.finalText, interim)
    }
    rec.onerror = (e) => {
      dlog('mic: recognition error', { error: e.error }, e.error === 'no-speech' || e.error === 'aborted' ? 'debug' : 'warn')
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
        this.fail('Microphone access is blocked. Click the mic icon in the address bar, allow it, then press "Try mic again".')
      } else if (e.error === 'audio-capture') {
        this.fail('No microphone was found. Check your mic (and macOS System Settings → Privacy → Microphone for your browser).')
      } else if (e.error === 'network') {
        this.fail("This browser's speech recognition service isn't reachable. Use Google Chrome, or type your answers.")
      }
      // "no-speech" and "aborted" are normal; onend restarts.
    }
    // Chrome ends recognition after a pause; keep listening while active.
    rec.onend = () => {
      if (!this.active || this.rec !== rec) return
      // Guard against a browser that ends recognition instantly, forever.
      const now = Date.now()
      this.restarts = [...this.restarts.filter((t) => now - t < 5000), now]
      dlog('mic: recognition ended, restarting', { restartsIn5s: this.restarts.length }, 'debug')
      if (this.restarts.length > 6) {
        this.fail("Speech recognition keeps stopping in this browser. Use Google Chrome, or type your answers.")
        return
      }
      this.begin(Ctor)
    }
    this.rec = rec
    try {
      rec.start()
    } catch {
      /* already started */
    }
  }

  /** Starts the transcript with words already heard (after a restart). */
  seed(text: string) {
    if (!this.active || !text.trim()) return
    this.finalText = text.trim() + ' '
    this.onChange(this.finalText, '')
  }

  /** Stops listening and returns everything heard since start(). */
  stop(): string {
    const wasActive = this.active
    this.active = false
    this.rec?.abort()
    this.rec = null
    if (wasActive) this.onListeningChange(false)
    const text = this.finalText.trim()
    if (wasActive) dlog('mic: stop', { chars: text.length })
    this.finalText = ''
    return text
  }

  get listening() {
    return this.active
  }
}

/**
 * Ask for the mic while we still have a click (so the browser shows its prompt
 * up front). Returns an error message, or null when the mic is usable.
 */
export async function requestMic(): Promise<string | null> {
  if (!navigator.mediaDevices?.getUserMedia) return null // let recognition ask on its own
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    stream.getTracks().forEach((t) => t.stop())
    dlog('mic: permission granted')
    return null
  } catch (e) {
    const name = (e as DOMException).name
    dlog('mic: permission problem', { name, message: (e as Error).message }, 'error')
    if (name === 'NotFoundError') return 'No microphone was found. You can still type your answers.'
    return 'Microphone access was denied. Allow it from the mic icon in the address bar, or type your answers.'
  }
}

const SENTENCE_END = /^([\s\S]*?[.?!])(\s+|$)/

type Spoken = { text: string; audio: Promise<Blob | null> }

/** When the server voice fails, use the browser voice for a while before trying again. */
let serverVoiceDownUntil = 0

/**
 * Fetch one sentence of Alex's voice from the backend (Kokoro). Returns null when
 * the server voice is unavailable, so the browser voice is used for that sentence.
 */
async function fetchServerVoice(text: string, signal: AbortSignal): Promise<Blob | null> {
  if (Date.now() < serverVoiceDownUntil) return null
  const started = performance.now()
  try {
    const r = await fetch('/api/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
      signal,
    })
    if (!r.ok) {
      serverVoiceDownUntil = Date.now() + 20_000
      dlog('voice: server voice unavailable, using browser voice', { status: r.status }, 'warn')
      return null
    }
    const blob = await r.blob()
    dlog('voice: server audio ready', { ms: Math.round(performance.now() - started), text: text.slice(0, 60) }, 'debug')
    return blob
  } catch (e) {
    if ((e as Error).name === 'AbortError') return null
    serverVoiceDownUntil = Date.now() + 20_000
    dlog('voice: server voice failed, using browser voice', { error: String(e) }, 'warn')
    return null
  }
}

/**
 * Speaks Alex's reply sentence by sentence while it streams in. Each sentence's
 * audio is requested from the server as soon as the sentence is complete, and
 * played in order; a sentence the server can't voice uses the browser voice.
 */
export class Speaker {
  private buffer = ''
  private queue: Spoken[] = []
  private playing = false
  private streamDone = false
  private muted = false
  private gen = 0 // bumped to cancel everything in flight
  private abort: AbortController | null = null
  private stopCurrent: (() => void) | null = null
  // Chrome can drop utterances that nothing references, and then never fires onend.
  private utterance: SpeechSynthesisUtterance | null = null
  enabled = true

  /** Called once everything for the current reply has been spoken (or skipped). */
  onIdle: () => void = () => {}

  begin() {
    this.cancelAll()
    this.buffer = ''
    this.streamDone = false
    this.muted = !this.enabled
  }

  push(chunk: string) {
    this.buffer += chunk
    let m: RegExpMatchArray | null
    // Speak each complete sentence; keep the unfinished tail.
    while ((m = this.buffer.match(SENTENCE_END)) && m[2] !== '') {
      this.say(m[1])
      this.buffer = this.buffer.slice(m[0].length)
    }
  }

  end() {
    if (this.buffer.trim()) this.say(this.buffer)
    this.buffer = ''
    this.streamDone = true
    this.checkIdle()
  }

  /** Stops speaking now (candidate interrupted). The rest of this reply stays silent. */
  interrupt() {
    if (this.speaking || this.buffer) dlog('voice: interrupted', { queued: this.queue.length })
    this.muted = true
    this.buffer = ''
    this.cancelAll()
    this.checkIdle()
  }

  get speaking() {
    return this.playing || this.queue.length > 0
  }

  private cancelAll() {
    this.gen++
    this.abort?.abort()
    this.abort = null
    this.queue = []
    this.stopCurrent?.()
    this.stopCurrent = null
    if (speechSupported.synthesis) window.speechSynthesis.cancel()
    this.utterance = null
    this.playing = false
  }

  private say(text: string) {
    const clean = text.trim()
    if (!clean) return
    if (this.muted) {
      dlog('voice: skipped sentence', { muted: true }, 'debug')
      return
    }
    if (!this.abort) this.abort = new AbortController()
    this.queue.push({ text: clean, audio: fetchServerVoice(clean, this.abort.signal) })
    if (!this.playing) void this.run(this.gen)
  }

  private async run(gen: number) {
    this.playing = true
    while (gen === this.gen && this.queue.length) {
      const item = this.queue[0]
      const blob = await item.audio
      if (gen !== this.gen) return
      dlog('voice: speaking', { text: item.text.slice(0, 80), via: blob ? 'server' : 'browser' })
      const played = blob ? await this.playAudio(blob) : false
      if (gen !== this.gen) return
      if (!played) await this.playBrowser(item.text)
      if (gen !== this.gen) return
      this.queue.shift()
    }
    this.playing = false
    this.checkIdle()
  }

  /** Plays server audio; resolves false if the browser refused to play it. */
  private playAudio(blob: Blob): Promise<boolean> {
    return new Promise((resolve) => {
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      let settled = false
      const finish = (ok: boolean) => {
        if (settled) return
        settled = true
        if (this.stopCurrent === stop) this.stopCurrent = null
        audio.onended = audio.onerror = null
        URL.revokeObjectURL(url)
        resolve(ok)
      }
      const stop = () => {
        audio.pause()
        finish(true)
      }
      this.stopCurrent = stop
      audio.onended = () => finish(true)
      audio.onerror = () => {
        dlog('voice: audio error', {}, 'warn')
        finish(false)
      }
      audio.play().catch((e) => {
        dlog('voice: audio play blocked', { error: String(e) }, 'warn')
        finish(false)
      })
    })
  }

  /** Browser speech for one sentence. Resolves when it ends, errors, or goes quiet. */
  private playBrowser(text: string): Promise<void> {
    if (!speechSupported.synthesis) return Promise.resolve()
    return new Promise((resolve) => {
      const synth = window.speechSynthesis
      const u = new SpeechSynthesisUtterance(text)
      u.rate = 1.0
      u.pitch = 1.0
      const voice = pickVoice()
      if (voice) u.voice = voice
      this.utterance = u
      let watchdog: number | null = null
      let settled = false
      const finish = () => {
        if (settled) return
        settled = true
        if (watchdog !== null) window.clearInterval(watchdog)
        if (this.stopCurrent === finish) this.stopCurrent = null
        if (this.utterance === u) this.utterance = null
        resolve()
      }
      this.stopCurrent = finish
      u.onend = finish
      u.onerror = (e) => {
        dlog('voice: utterance error', { error: (e as SpeechSynthesisErrorEvent).error }, 'warn')
        finish()
      }
      synth.resume() // Chrome sometimes leaves synthesis paused
      synth.speak(u)
      // If the browser loses the end event, notice that it went quiet anyway.
      window.setTimeout(() => {
        if (settled) return
        watchdog = window.setInterval(() => {
          if (!synth.speaking && !synth.pending) {
            dlog('voice: browser lost an end event; recovering', {}, 'warn')
            finish()
          }
        }, 500)
      }, 1000)
    })
  }

  private checkIdle() {
    if (this.streamDone && !this.speaking) {
      this.streamDone = false // fire once per reply
      dlog('voice: finished reply')
      this.onIdle()
    }
  }
}

/**
 * Alex speaks with a female voice. The most natural voices come first: macOS
 * "Premium" / "Enhanced" voices (free downloads in System Settings > Accessibility >
 * Spoken Content > System voice > Manage Voices), then Chrome's Google voice, then
 * the standard macOS and Windows female voices.
 */
const FEMALE_VOICES = [
  'Ava', 'Zoe', 'Allison', 'Susan', 'Samantha', 'Serena', 'Kate', 'Stephanie', 'Karen', 'Moira', 'Tessa',
  'Veena', 'Isha', 'Catherine', 'Nicky', 'Joelle', 'Kathy', 'Shelley', 'Sandy', 'Flo',
]
const WINDOWS_FEMALE = ['Microsoft Aria', 'Microsoft Jenny', 'Microsoft Zira', 'Microsoft Libby', 'Microsoft Sonia']

function voiceScore(v: SpeechSynthesisVoice): number {
  if (!v.lang.toLowerCase().startsWith('en')) return -1
  const name = v.name
  const base = name.replace(/\s*\(.*\)\s*$/, '').trim()
  const quality = /premium/i.test(name) ? 300 : /enhanced/i.test(name) ? 200 : /natural|neural|online/i.test(name) ? 150 : 0
  const female = FEMALE_VOICES.indexOf(base)
  if (female >= 0) return quality + 100 - female
  if (name === 'Google US English') return 120
  if (name === 'Google UK English Female') return 110
  const win = WINDOWS_FEMALE.findIndex((w) => name.startsWith(w))
  if (win >= 0) return quality + 90 - win
  return -1
}

let cachedVoice: SpeechSynthesisVoice | null | undefined
function pickVoice(): SpeechSynthesisVoice | null {
  if (cachedVoice !== undefined && cachedVoice !== null) return cachedVoice
  const voices = window.speechSynthesis.getVoices()
  if (!voices.length) return null
  const ranked = voices
    .map((v) => ({ v, score: voiceScore(v) }))
    .filter((x) => x.score >= 0)
    .sort((a, b) => b.score - a.score)
  cachedVoice = ranked[0]?.v ?? voices.find((v) => v.lang.startsWith('en')) ?? null
  dlog('voice: chosen', {
    name: cachedVoice?.name,
    options: ranked.slice(0, 5).map((x) => x.v.name),
  })
  return cachedVoice
}

// Chrome loads its voice list asynchronously; pick as soon as it is ready so the
// first sentence already uses the chosen voice.
if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
  window.speechSynthesis.addEventListener?.('voiceschanged', () => {
    cachedVoice = undefined
    pickVoice()
  })
  pickVoice()
}
