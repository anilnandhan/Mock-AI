# Mock-AI

Practise a real coding interview out loud. Pick a DSA problem, and an AI interviewer ("Ren") runs a 45-minute interview with you by voice: clarifying questions, approach, coding, dry run, follow-up. Your C++ code is never run. Ren reads and traces it, like an interviewer reading a shared doc, and you get a detailed report at the end.

See [docs/PLAN.md](docs/PLAN.md) for the full design.

## Requirements

Works on macOS and Windows (and Linux).

| | macOS | Windows |
|---|---|---|
| **Git** | Comes with Xcode tools (`xcode-select --install`) | https://git-scm.com/download/win |
| **uv** (installs Python 3.12 for you) | `brew install uv`, or `curl -LsSf https://astral.sh/uv/install.sh \| sh` | In PowerShell: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| **Node.js 20+** | `brew install node`, or https://nodejs.org | https://nodejs.org (LTS installer) |
| **Google Chrome** | Needed for speech recognition | Needed for speech recognition |

You also need an API key for one AI provider:
- **Anthropic** (Claude, recommended): https://console.anthropic.com/settings/keys
- **Groq** (cheap and fast, fine for a quick try): https://console.groq.com/keys

Close and reopen your terminal after installing uv or Node so the commands are found. Check with `uv --version` and `node --version`.

## Setup (once)

**macOS (Terminal):**

```bash
git clone https://github.com/anilnandhan/Mock-AI.git
cd Mock-AI/backend
cp .env.example .env
uv sync
cd ../frontend
npm install
```

**Windows (PowerShell):**

```powershell
git clone https://github.com/anilnandhan/Mock-AI.git
cd Mock-AI\backend
Copy-Item .env.example .env
uv sync
cd ..\frontend
npm install
```

Then open `backend/.env` in any text editor and paste your key after `ANTHROPIC_API_KEY=` (or set `LLM_PROVIDER=groq` and fill in `GROQ_API_KEY=`). Never commit this file or share your key.

## Run

Use two terminal windows, both started from the `Mock-AI` folder.

**Terminal 1, backend** (same command on macOS and Windows):

```bash
cd backend
uv run uvicorn app.main:app --reload --port 8000
```

The first start downloads Ren's voice model (about 350 MB) into `backend/models/`. That takes a minute or two; wait for `voice: ready` in the output. Until then the browser's own voice is used.

**Terminal 2, frontend:**

```bash
cd frontend
npm run dev
```

Open http://localhost:5173 in **Chrome**, pick a problem, click **Join interview**, and allow the microphone. Headphones give the best results.

To stop, press `Ctrl+C` in each terminal.

### If something goes wrong

| Problem | Fix |
|---|---|
| `uv` or `npm` "not recognized" | Close and reopen the terminal after installing; on Windows, reopen PowerShell |
| Windows: "running scripts is disabled" when running `npm` | Run once in PowerShell: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| "address already in use" / port 8000 or 5173 busy | Another copy is still running; close it, or restart the computer |
| Ren doesn't answer, or an error about the API key | Check the key in `backend/.env`, then restart the backend |
| Mic not working | Use Chrome, click the mic icon in the address bar and allow it, then **Try mic again** |
| Ren sounds robotic | The voice model is still downloading or failed; look for `voice:` lines in the backend output |
| Anything else | Send `backend/logs/mock_ai.log`. It contains what you said in your interviews, but not your API key |

## AI providers

Set `LLM_PROVIDER` in `backend/.env`:

| Provider | Interview model | Report model | Notes |
|---|---|---|---|
| `groq` | `openai/gpt-oss-120b` | `openai/gpt-oss-120b` | Cheap and fast. The free tier has low token-per-minute limits, so long interviews may hit them; set `INTERVIEW_MINUTES=15` while testing |
| `anthropic` | `claude-sonnet-5-5` | `claude-opus-5-5` | Intended production setup |

Override models with `INTERVIEWER_MODEL` and `REPORT_MODEL`. Each interview keeps the provider it started with, so switching providers only affects new interviews. Provider code lives in `backend/app/providers/`.

## How to use

- Pick a problem, then click **Join interview**. Ren introduces the problem.
- The screen is laid out like a video call: the shared C++ editor in the middle, Ren's and your tiles on the right (a green ring shows who is talking), and live captions under the editor. Ren's words appear as she says them.
- The control bar at the bottom: voice on/off, camera (an optional self-view only you see; nothing is recorded), captions, problem statement, transcript (also where you type), auto-send, and **End**. Panel and caption choices are remembered in this browser.
- Just talk. When you pause, your turn sends itself after a short countdown. The pause it waits for depends on your last words: about 1.5 s normally, 1 s after a question ("…right?", "I'm done"), 4 s after a trailing "and / so / because", 12 s after "let me think", and longer while coding. Typing in the editor resets the timer.
- Press **Hold** during the countdown to keep the turn open; speaking again releases it. **Send** / **Ctrl/Cmd + Enter** still send immediately, and a typed answer is always sent by hand.
- If your turn was sent too early, just keep talking:
  - While Ren is still thinking, her reply is skipped (shown faded as "cut off") and your new words continue your answer.
  - Once she is speaking, talking over her stops her (barge-in). Her own voice coming through the speakers is ignored by comparing what the mic hears with what she is saying; headphones make this most reliable.
  - Either way the next turn tells Ren you didn't hear her last reply. And if a turn clearly stops mid-sentence, Ren only says "Mm-hm" or "Go on."
- Turn this off with **⏱ Auto-send** in the top bar (remembered in this browser). The rules live in `frontend/src/lib/turns.ts`.
- Press **Esc** to interrupt Ren.
- Ren's voice is made on your own machine by [Kokoro](https://github.com/hexgrad/kokoro), a free open-source voice model (see **Voice** below). If it isn't available, the browser's own voice is used instead.
- If you are silent and not typing for 3 minutes, Ren checks in.
- At the end, click **View my report** (when reports are on).

Reports are **off by default** while we test the conversation (each one is a paid call to the report model). To turn them on, create `frontend/.env.local` with `VITE_ENABLE_REPORTS=true` and restart `npm run dev`.

## Voice

Ren speaks with Kokoro's `af_heart` voice, generated by the backend (`backend/app/tts.py`, endpoint `POST /api/tts`). It is free and works offline. The first time the backend starts, it downloads the model (about 350 MB) into `backend/models/`; until then, or if it fails, the browser voice is used. Each sentence takes about 0.3 to 0.7 s to make on a Mac CPU and is requested as soon as Ren finishes writing it.

Settings in `backend/.env`:

| Setting | Default | Notes |
|---|---|---|
| `TTS_VOICE` | `af_heart` | Others: `af_sarah`, `af_bella`, `af_aoede`, `bf_emma` (British), `hf_alpha` / `hf_beta` (Indian accent) |
| `TTS_SPEED` | `1.0` | For example `0.9` slower, `1.1` faster |
| `TTS_ENABLED` | `true` | `false` uses the browser voice only |

Restart the backend after changing them. `GET /api/health` shows the voice and whether it is ready; the log line `voice: ready` confirms it loaded.

## Debugging

Everything goes to `backend/logs/mock_ai.log`: each turn on the server (`app.*`) and what the browser did (`client`: mic, speech, turn-taking). Lines carry the first 8 characters of the interview id in brackets.

```bash
tail -f backend/logs/mock_ai.log
```

Useful things to search for:

| Search | Means |
|---|---|
| `429` or `Retrying request` | The AI provider rate-limited us; the turn waits and retries |
| `slow turn` | A turn took over 8 seconds |
| `mic:` | Mic start/stop, what was heard, permission errors |
| `turn not sent` / `send ignored` | Send was pressed when the app wasn't waiting for you |
| `empty reply` / `no valid update_interview` | The model answered badly |
| `voice:` | Voice loading on the server; in the browser, `via: server` or `via: browser` per sentence |

Set `LOG_LEVEL=DEBUG` in `backend/.env` to also log full prompts and raw model replies. Browser events are also in Chrome's console, prefixed `[mock-ai]`.

## Tests

```bash
cd backend && uv run pytest
```

Tests use fake Anthropic and Groq clients, so they need no API key.

## Project layout

```
backend/
  app/main.py          API routes
  app/interviewer.py   one interview turn: prompt, streaming, state
  app/providers/       Anthropic and Groq adapters (same interface)
  app/prompts.py       interviewer + report instructions, tool and schema
  app/report.py        end-of-interview grading
  app/tts.py           Ren's voice (Kokoro)
  problems/*.json      problem bank (public + private interviewer notes)
frontend/
  src/pages/           Home, Interview, Report
  src/lib/api.ts       API client and streaming
  src/lib/speech.ts    speech-to-text and text-to-speech
```

## Adding a problem

Copy `backend/problems/best-time-to-buy-sell-stock.json`, keep the same fields, and make the `id` match the file name. Write the statement in your own words. The backend validates every file at startup.
