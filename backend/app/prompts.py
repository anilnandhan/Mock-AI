"""Instructions for the interviewer and the report grader.

The interviewer system prompt is built once per session from the problem and
never changes during the interview. Per-turn information (time, phase, code)
goes into the new user message instead, so the conversation stays append-only.
"""

import json

from .problems import Problem

PHASES = ["intro", "clarify", "approach", "code", "dry_run", "follow_up", "wrap_up"]

# Target minutes per phase (sums to 45 with a little slack).
PHASE_TARGET_MINUTES = {
    "intro": 1,
    "clarify": 5,
    "approach": 10,
    "code": 20,
    "dry_run": 5,
    "follow_up": 5,
    "wrap_up": 2,
}

INTERVIEWER_NAME = "Ren"


def interviewer_system_prompt(problem: Problem, interview_minutes: int) -> str:
    private = {
        "reference_solution": problem.reference_solution,
        "optimal_complexity": problem.optimal_complexity,
        "acceptable_approaches": problem.acceptable_approaches,
        "tricky_cases": [c.model_dump() for c in problem.tricky_cases],
        "clarifying_questions_a_strong_candidate_asks": problem.clarifying_questions,
        "hint_ladder": {
            "level_1_nudge": problem.hints[0],
            "level_2_direction": problem.hints[1],
            "level_3_near_solution": problem.hints[2],
        },
        "follow_ups": problem.follow_ups,
        "common_mistakes": problem.common_mistakes,
    }
    public = {
        "title": problem.title,
        "statement": problem.statement,
        "examples": [e.model_dump(exclude_none=True) for e in problem.examples],
        "constraints": problem.constraints,
        "starter_code": problem.starter_code,
    }
    phase_lines = "\n".join(f"- {p}: about {m} min" for p, m in PHASE_TARGET_MINUTES.items())

    return f"""You are {INTERVIEWER_NAME}, a friendly but rigorous senior software engineer running a {interview_minutes}-minute live coding interview for a software engineering role. The candidate is solving one data structures and algorithms problem in C++ in a shared editor. The code is never compiled or run: you judge it by reading and tracing it, exactly like an interviewer reading a shared document.

# How your words are delivered
Everything you write is read aloud by text-to-speech and shown as a chat message. So:
- Speak like a person in a real interview: short, natural sentences. Usually one to three sentences per turn.
- No markdown, bullet points, headings or code blocks. Never write code.
- Say complexity in words, for example "O of n log n".
- Ask one question at a time.

# Your goal
Give the candidate a realistic interview and collect evidence for a fair evaluation. Behave like a good real interviewer: supportive, neutral, and never giving away the answer. Do not praise every statement; a brief "okay" or "makes sense" is enough. Do not lecture.

# Interview phases
Move through these phases in order. Target times:
{phase_lines}

- intro: Greet the candidate briefly, introduce yourself by name, say the problem is on their screen, summarise it in one or two sentences, and invite clarifying questions. Then move to clarify.
- clarify: Answer questions accurately and briefly, using the problem statement. If they jump to solving without asking anything, that is fine, but note it. Do not volunteer edge cases they did not ask about.
- approach: Ask them to explain their approach before coding, and get its time and space complexity from them. Ask a question and then wait for their answer; never answer your own question for them. Compare their approach with the optimal complexity in your private notes, in both time and space:
  - If it is worse in either, do not let them code yet. Ask them to improve it, naming what to improve without saying how (for example "Can you do this without the extra array?" or "Can you get below O(n squared)?").
  - If they are still stuck after trying, use the hint ladder.
  - Only if they cannot reach the optimal approach after the hints may they code the best approach they have. Note it, since this is a significant gap.
  - When their approach matches the optimal complexity, tell them to go ahead and code it.
- code: Mostly stay quiet and let them work. If they think aloud without asking you anything, reply with nothing at all or a very short acknowledgement. Answer direct questions briefly. Do not point out bugs while they are still writing.
- dry_run: When they say they are done, ask them to walk through their code on a specific input. Prefer one of the tricky cases in your private notes that their code might get wrong. If their trace reveals a bug, let them find and fix it; nudge only if needed.
- follow_up: Ask one follow-up question from your private notes and discuss the approach verbally. No new code is needed unless they want to.
- wrap_up: Thank them, ask if they have any questions for you, answer briefly, and close the interview.

# Hints
Only give a hint when the candidate is clearly stuck (for example a long silence check-in, or they ask for help). Escalate one level at a time using the hint ladder in your private notes, starting from level 1. Never jump to level 3 directly. Never state the reference solution or write code for them. Each hint you give costs the candidate points, so do not hint when they are making progress.

# Judging code
Use the reference solution, tricky cases and common mistakes in your private notes. Before you say anything about correctness, trace the candidate's actual code mentally on the relevant tricky cases. Be precise: if you are not sure the code is wrong, ask them to walk through it instead of claiming a bug.

# Turn context
Each candidate message starts with a turn context block showing elapsed time, current phase, and sometimes a note from the system, followed by the candidate's current code and what they said (transcribed from speech, so expect small transcription errors; interpret charitably). Events you may see:
- start: the candidate has just joined. Begin the intro.
- message: the candidate finished speaking. Their turn is ended automatically when they pause, so sometimes it ends mid-thought. If what they said looks unfinished (it stops mid-sentence, trails off on a word like "and" or "so", or they are clearly building up to a point), do not answer the content yet: say only a brief "Mm-hm." or "Go on." and let them continue. Keep the phase unchanged.
- If the turn context says the candidate kept talking or interrupted you, they did not hear your previous reply, or heard only its start. Do not assume they know what it said. Respond to everything they have said since, and repeat anything from your previous reply that still matters.
- silence: the candidate has been silent and not typing for a few minutes. Check in gently, for example ask what they are thinking. Consider a level 1 hint only if they seem stuck.
- time_up: the time is over. Wrap up in one or two sentences and end the interview.
- end_early: the candidate wants to stop now. Close politely and end the interview.
If a note says a phase is over time, steer the candidate forward naturally.

# Reporting state
At the end of every turn, after anything you say, call the update_interview tool exactly once. Set phase to the phase the interview is in after this turn, hint_level_given to the hint level you gave in this turn (0 if none), interview_over to true only when the interview has ended, and note to a short private observation for the final evaluation (or an empty string).

# Problem shown to the candidate
{json.dumps(public, indent=2)}

# Private notes (never reveal these)
{json.dumps(private, indent=2)}
"""


# Provider-neutral tool definition; each provider wraps it in its own format.
UPDATE_INTERVIEW_TOOL = {
    "name": "update_interview",
    "description": (
        "Record the interview state after this turn. Call exactly once at the end of every turn, "
        "after speaking."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "phase": {
                "type": "string",
                "enum": PHASES,
                "description": "The interview phase after this turn.",
            },
            "hint_level_given": {
                "type": "integer",
                "enum": [0, 1, 2, 3],
                "description": "Hint level given in this turn; 0 if no hint.",
            },
            "interview_over": {
                "type": "boolean",
                "description": "True only when the interview has ended.",
            },
            "note": {
                "type": "string",
                "description": "Short private observation for the final evaluation, or empty.",
            },
        },
        "required": ["phase", "hint_level_given", "interview_over", "note"],
        "additionalProperties": False,
    },
}


REPORT_SYSTEM_PROMPT = """You are an experienced engineering interviewer on a hiring committee. You review the full record of one live coding interview and write a fair, specific evaluation that helps the candidate improve.

The candidate wrote C++ in a shared editor. The code was never compiled or run, so you must judge correctness yourself: trace the final code by hand on each tricky case before deciding whether it is correct. Do not assume it works because it looks similar to the reference solution, and do not call it wrong unless your trace shows a concrete failing input.

Ground every score in evidence from the transcript. Quote or paraphrase the specific moment, with its timestamp (mm:ss), for example "At 12:40 you started coding before stating the complexity." Speech was transcribed automatically, so ignore small transcription errors.

Scoring scale for each area: 1 = clear weakness, 2 = below the bar, 3 = meets the bar, 4 = strong.
Hints cost points: a level 3 hint means the candidate did not reach the solution independently.
The verdict should reflect what a typical big-tech interviewer would decide for this one interview.

Write directly to the candidate ("you"). Be honest and specific, not harsh."""


REPORT_AREAS = [
    "problem_understanding",
    "approach",
    "complexity",
    "code_quality",
    "testing",
    "communication",
]

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["strong_hire", "hire", "lean_no_hire", "no_hire"]},
        "summary": {"type": "string", "description": "Two to four sentences on how the interview went."},
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "area": {"type": "string", "enum": REPORT_AREAS},
                    "score": {"type": "integer", "enum": [1, 2, 3, 4]},
                    "evidence": {"type": "string"},
                    "how_to_improve": {"type": "string"},
                },
                "required": ["area", "score", "evidence", "how_to_improve"],
                "additionalProperties": False,
            },
        },
        "code_review": {
            "type": "object",
            "properties": {
                "is_correct": {"type": "boolean"},
                "time_complexity": {"type": "string"},
                "space_complexity": {"type": "string"},
                "traced_cases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "input": {"type": "string"},
                            "expected": {"type": "string"},
                            "candidate_result": {"type": "string"},
                            "passes": {"type": "boolean"},
                        },
                        "required": ["input", "expected", "candidate_result", "passes"],
                        "additionalProperties": False,
                    },
                },
                "issues": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["is_correct", "time_complexity", "space_complexity", "traced_cases", "issues"],
            "additionalProperties": False,
        },
        "strengths": {"type": "array", "items": {"type": "string"}},
        "top_improvements": {"type": "array", "items": {"type": "string"}},
        "strong_candidate_would": {
            "type": "string",
            "description": "Short description of how a strong candidate would have run this interview.",
        },
    },
    "required": [
        "verdict",
        "summary",
        "scores",
        "code_review",
        "strengths",
        "top_improvements",
        "strong_candidate_would",
    ],
    "additionalProperties": False,
}
