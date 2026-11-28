/**
 * Automatic end-of-turn: decides how long a pause means "I'm done talking".
 *
 * Combines three signals:
 * - how long the candidate has been quiet (no speech, no typing),
 * - what they said last (trailing "and…" means more is coming; a question means they want an answer),
 * - the interview phase (while coding, people pause a lot mid-thought).
 */

/** Words people say right before they keep going. */
const CONTINUING =
  /\b(and|so|but|because|cause|then|like|um+|uh+|hmm+|the|a|an|to|of|if|or|with|for|which|that|where|when|while|post that|after that|basically|actually)\s*$/i

/** Phrases that ask for time to think. */
const THINKING = /\b(let me think|give me a (sec|second|minute|moment)|one (sec|second|moment)|hold on|wait|let me see|thinking)\s*\.?$/i

/** Phrases that hand the turn to the interviewer. */
// "right" counts only as a tag question ("...on a later day right"), not "to the right".
const QUESTION =
  /(\?|(?<!\b(the|to|its|on|your|my)\s)\bright|\bcorrect|\bokay|\bdoes that (make sense|work|sound)( good| okay| right)?|\bis that (ok|okay|right|correct|fine)|\bwhat do you think|\bshould i|\bcan i|\bthat'?s it|\bthat is it|\b(i'?m|i am|i'?ve|i have|i)( just)? (done|finished|completed)(\s+[\w']+){0,5})\s*\.?$/i

export type PauseRule = 'question' | 'normal' | 'continuing' | 'thinking'

export function pauseRule(lastWords: string): PauseRule {
  const t = lastWords.trim()
  if (THINKING.test(t)) return 'thinking'
  if (QUESTION.test(t)) return 'question'
  if (CONTINUING.test(t)) return 'continuing'
  return 'normal'
}

/**
 * Barge-in with an echo guard. While Alex speaks the mic stays on, so it also
 * hears Alex through the speakers. Only treat it as the candidate talking when
 * enough of the heard words are NOT in what Alex is saying.
 */
export function isBargeIn(
  heard: string,
  alexSaid: string,
): { yes: boolean; newWords: string[]; candidateText: string } {
  const words = (t: string) => t.toLowerCase().match(/[a-z0-9']+/g) ?? []
  const said = new Set(words(alexSaid))
  const all = words(heard)
  // Judge only the most recent words: the mic has been hearing Alex's voice since
  // he started, and that echo would otherwise outweigh a real interruption.
  const heardWords = all.slice(-12).filter((w) => w.length > 1)
  const newWords = heardWords.filter((w) => !said.has(w))
  // The candidate's words: everything from the first word Alex didn't say,
  // kept in order even where they happen to overlap with Alex's words.
  const first = all.findIndex((w) => w.length > 1 && !said.has(w))
  const candidateText = first < 0 ? '' : all.slice(first).join(' ')
  return { yes: newWords.length >= 4 && newWords.length >= heardWords.length / 2, newWords, candidateText }
}

/** Milliseconds of quiet after the last word (or keystroke) before the turn is sent. */
export function pauseNeeded(lastWords: string, phase: string): { ms: number; rule: PauseRule } {
  const rule = pauseRule(lastWords)
  const coding = phase === 'code'
  const ms = {
    question: 1200,
    normal: coding ? 3500 : 2000,
    continuing: coding ? 6000 : 4000,
    thinking: 12000,
  }[rule]
  return { ms, rule }
}
