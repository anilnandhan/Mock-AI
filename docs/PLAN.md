# Mock-AI: POC Build Plan

An AI interviewer that runs a realistic 45-minute DSA interview by voice. The candidate picks a topic and difficulty, gets a problem and a code editor, and talks through it with the AI. Code is **not executed**. The AI judges the conversation and the code, like a real interviewer reading a shared doc.

Status: **steps 0–5 built; not yet tested against the live AI (needs an API key). Step 6 (testing with people) next.**

Confirmed choices: Sonnet 5.5 for the live interview, Opus 5.5 for the report, uv for Python, first problem Best Time to Buy and Sell Stock, interviewer "Alex", a friendly senior engineer.

---

## 1. Decisions so far

| Topic | Decision |
|---|---|
| Candidate language | C++ first; Python and Java later |
| Code execution | None. The AI reads and traces the code |
| Backend | Python + FastAPI |
| Frontend | React + Vite + TypeScript (thin UI only) |
| Transport | Plain HTTP + server-sent events for streaming. No WebSockets |
| Voice (POC) | Browser speech recognition and speech synthesis (Chrome) |
| Turn-taking | Candidate ends their turn (Send button or Ctrl+Enter). AI checks in after long silence |
| Problems | Our own JSON files, own wording, reviewed by hand. No scraping |

---

## 2. Tech stack

| Part | Choice |
|---|---|
| Python | 3.12, managed by **uv** (the system Python is 3.9, too old) |
| Web framework | FastAPI + Uvicorn |
| AI SDK | `anthropic` (official Python SDK), async client |
| AI provider | Selectable with `LLM_PROVIDER`: `groq` (`openai/gpt-oss-120b`) for early testing, `anthropic` for production |
| Interviewer model | `claude-sonnet-5-5`, low effort for fast replies |
| Report model | `claude-opus-5-5`, high effort for careful grading |
| Database | SQLite via SQLModel |
| Config | `.env` file with `ANTHROPIC_API_KEY` (you add it yourself) |
| Frontend | React 18 + Vite + TypeScript |
| Editor | Monaco (`@monaco-editor/react`), C++ mode |
| Styling | Plain CSS (no Tailwind needed for the POC) |

Both models get the server-side refusal fallback (`fallbacks: "default"`), so a rare false-positive safety refusal does not break an interview.

---

## 3. Folder structure

```
Mock-AI/
  backend/
    app/
      main.py           FastAPI app and routes
      config.py         settings from .env
      db.py             SQLite engine and session
      models.py         Session, Message, Report tables
      problems.py       loads and validates problem JSON
      interviewer.py    builds prompts, calls Claude, streams replies
      prompts.py        interviewer and report instructions
      report.py         end-of-interview grading
      schemas.py        request and response shapes
    problems/
      best-time-to-buy-sell-stock.json
    tests/
    pyproject.toml
    .env.example
  frontend/
    src/
      pages/Home.tsx        topic and difficulty picker
      pages/Interview.tsx   problem | editor | conversation
      pages/Report.tsx      final report
      lib/api.ts            HTTP + streaming client
      lib/speech.ts         speech-to-text and text-to-speech
      lib/turns.ts          turn-taking state machine
    package.json
  docs/
    PLAN.md
  README.md
```

---

## 4. Interview flow

### Turn-taking (browser)

```
AI SPEAKING ──speech ends──► LISTENING ──Send / Ctrl+Enter / silence──► AI THINKING ──first words──► AI SPEAKING
     ▲                          │
     └── candidate interrupts ──┘  (speech stops, back to LISTENING)
```

- Only the candidate ends their turn. The AI never cuts in mid-thought.
- Typing is never a turn. Silent coding for 10 minutes is fine.
- Silence check-in: no speech **and** no typing for 3 minutes sends a `silence` event.
- Mic is off while the AI speaks, to avoid transcribing its own voice.

### Interview phases (backend)

| Phase | Target time | AI behaviour |
|---|---|---|
| intro | 1 min | Greets, presents the problem, invites questions |
| clarify | 5 min | Answers questions; notes missed edge cases |
| approach | 10 min | Probes brute force → optimal, asks for complexity |
| code | 20 min | Mostly quiet; short acknowledgements |
| dry_run | 5 min | Asks for a walkthrough on a tricky input |
| follow_up | 5 min | One harder variation |
| wrap_up | 2 min | Closes, answers candidate questions |

If a phase runs over, the backend adds a note telling the AI to move things along.

### One turn, end to end

1. Browser converts speech to text and sends `POST /api/sessions/{id}/turn`.
2. Backend loads the session, history, phase and latest code, then calls Claude.
3. Spoken text streams back as server-sent events.
4. Browser shows each chunk and speaks each finished sentence.
5. At the end, the AI reports interview state through a tool call (`update_interview`): next phase, hint level used, whether to end. The backend saves it.

---

## 5. API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/problems` | List problems (id, title, topic, difficulty) |
| POST | `/api/sessions` | Start an interview `{problem_id}` → session + streamed opening |
| POST | `/api/sessions/{id}/turn` | `{event, text, code, elapsed_seconds}` → streamed reply |
| POST | `/api/sessions/{id}/end` | End early |
| POST | `/api/sessions/{id}/report` | Generate and return the report |
| GET | `/api/sessions/{id}` | Session, transcript and report |

`event` is one of `message`, `silence`, `time_up`.

Stream events:

```
data: {"type": "text", "text": "Good question — "}
data: {"type": "text", "text": "yes, the array can be empty."}
data: {"type": "state", "phase": "approach", "hint_level": 0, "end": false}
data: {"type": "done"}
```

---

## 6. Data model

```
Session   id, problem_id, phase, hint_level_max, started_at, ended_at, status
Message   id, session_id, role (ai | candidate | event), text, code_snapshot, phase, created_at
Report    id, session_id, scores_json, verdict, summary, created_at
```

---

## 7. Problem file format

```json
{
  "id": "best-time-to-buy-sell-stock",
  "title": "Best Time to Buy and Sell Stock",
  "topic": "arrays",
  "difficulty": "easy",
  "statement": "...",
  "examples": [{"input": "...", "output": "...", "explanation": "..."}],
  "constraints": ["..."],
  "starter_code": "class Solution {\npublic:\n    int maxProfit(vector<int>& prices) {\n        \n    }\n};",
  "reference_solution": "...",
  "optimal_complexity": "O(n) time, O(1) space",
  "acceptable_approaches": ["one pass tracking min price"],
  "tricky_cases": [{"input": "[]", "expected": "0", "why": "empty input"}],
  "clarifying_questions": ["Can the array be empty?"],
  "hints": ["nudge", "direction", "near-solution"],
  "follow_ups": ["What if you could make at most two transactions?"],
  "common_mistakes": ["selling before buying"]
}
```

The candidate sees only `title`, `statement`, `examples`, `constraints` and `starter_code`. Everything else is private to the AI.

---

## 8. Report rubric

| Area | 1–4 score with quoted evidence |
|---|---|
| Problem understanding | Clarifying questions, constraints, edge cases |
| Approach | Brute force first, path to optimal, tradeoffs |
| Complexity | Correct time and space analysis |
| Code quality | Correctness (traced on tricky cases), readability |
| Testing | Dry run, finds own bugs |
| Communication | Thinks aloud, uses hints well |
| **Verdict** | Strong hire / Hire / Lean no hire / No hire |

Each score cites a moment: "At 12:40 you started coding before stating the complexity."

---

## 9. Build steps

### Step 0: Setup
- Install `uv` (`brew install uv`); uv installs Python 3.12 for the project
- Scaffold `backend/` (FastAPI) and `frontend/` (Vite React TS)
- `.env.example`, `.gitignore`, run scripts in README
- **Done when:** `/api/health` returns ok and the frontend loads

### Step 1: Problems
- Problem schema with validation
- First problem: Best Time to Buy and Sell Stock
- `GET /api/problems`
- **Done when:** the problem loads and invalid files are rejected

### Step 2: Interviewer backend (text)
- Sessions and messages in SQLite
- Interviewer prompt: persona, phases, hint ladder, never reveal the solution, short spoken-style replies
- Streaming turn endpoint with the `update_interview` tool for state
- Phase timing notes
- **Done when:** a full interview works through the API with curl

### Step 3: Interview screen (text)
- Three panels: problem, Monaco editor, conversation
- Timer and phase indicator
- Typed messages with streaming replies
- **Done when:** a full interview works by typing in the browser

### Step 4: Voice
- Speech-to-text with live transcript
- Sentence-by-sentence speech of the AI's replies
- Turn-taking state machine, Send / Ctrl+Enter, interruptions
- Silence check-in
- **Done when:** a full interview works by voice in Chrome

### Step 5: Report
- Opus grading from the transcript and code snapshots
- Report page with scores, evidence, verdict and suggestions
- **Done when:** an interview ends with a useful report

### Step 6: Test with people
- Run 3–5 interviews (you + friends)
- Note where the AI was too helpful, too quiet, wrong, or robotic
- Tune the prompt

**Estimated time:** about 1 week for steps 0–5.

---

## 10. Later (not in the POC)

- 20 problems across 3 topics, plus the topic/difficulty picker
- Automatic turn detection (silence + typing + last words)
- Deepgram + Cartesia voice through Pipecat
- Python and Java
- Accounts, history, progress across sessions
- Background test execution, only if testers say grading was wrong

---

## 11. Needs your confirmation

1. Models: Sonnet 5.5 for the live interview, Opus 5.5 for the report?
2. OK to install `uv` with Homebrew?
3. First problem: Best Time to Buy and Sell Stock?
4. Interviewer persona: a name and tone (for example "Alex, friendly senior engineer")?
