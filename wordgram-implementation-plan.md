# wordgram — Implementation Plan

Execution handoff for the `wordgram` project. Read
`wordgram-functional-description.md` first — it is the source of truth for
*what* to build; this plan says *how*. Where the two disagree, the
functional description wins.

## Where the work happens

Repository: `github.com/andgineer/wordgram`. It already contains the
0.0.1 scaffold: hatchling packaging with the version in
`src/wordgram/__about__.py`, `src` layout, pytest, `uv.lock`,
CI workflow (`.github/workflows/ci.yml`, runs `uv sync --frozen` +
`uv run pytest tests/`), and a publish workflow triggered by `v*` semver
tags. Build on top of it; do not restructure the packaging.

Rules:

- Python 3.12+. All imports at module top level. English-only comments
  and docs.
- Every new module gets tests in the same commit. `uv run pytest` must be
  green after every milestone.
- No real network, no real Telegram/Anki/agent in tests — fake or mock
  every boundary.
- Update `uv.lock` when adding dependencies (`uv lock`); CI uses
  `--frozen`.

## Technology choices (fixed)

| Concern | Choice |
|---|---|
| Telegram framework | `python-telegram-bot` v21+ (async, long polling) |
| HTTP client (AnkiConnect, dictionary) | `httpx` (async) |
| TTS fallback | `edge-tts` (MS Edge voices, free, outputs mp3) |
| Dictionary pronunciation | `https://api.dictionaryapi.dev/api/v2/entries/en/{word}` — take the first `phonetics[].audio` non-empty URL (they are Wiktionary recordings); prefer entries whose URL contains the configured accent (`-us` / `-uk`), else any |
| Settings | `pydantic-settings`, env prefix `WORDGRAM_`, `.env` support |
| Persistent queue | stdlib `sqlite3`, single DB file |
| LLM | CLI agent subprocess; three supported agents — `claude` (default), `antigravity` (the `agy` CLI running Gemini models), `gemini` (Google's Gemini CLI); see Milestone 2 |
| Lint | `ruff` (line-length 99), run in CI after tests |

## Configuration (env vars)

| Variable | Meaning | Default |
|---|---|---|
| `WORDGRAM_BOT_TOKEN` | Telegram bot token | required |
| `WORDGRAM_ALLOWED_USER_IDS` | comma-separated Telegram user IDs | required |
| `WORDGRAM_AGENT` | `claude`, `antigravity`, or `gemini` | `claude` |
| `WORDGRAM_CLAUDE_CMD` | claude argv template | `claude -p {prompt} --model {model} --output-format stream-json --include-partial-messages --verbose` |
| `WORDGRAM_ANTIGRAVITY_CMD` | antigravity argv template | `agy --model {model} --dangerously-skip-permissions -p {prompt}` |
| `WORDGRAM_GEMINI_CMD` | gemini argv template | `gemini --model {model} -p {prompt}` |
| `WORDGRAM_MODEL` | model substituted into the template | per agent: `haiku` (claude), `gemini-3.5-flash` (antigravity, gemini) |
| `WORDGRAM_AGENT_TIMEOUT` | seconds | `120` |
| `WORDGRAM_ANKI_URL` | AnkiConnect endpoint | `http://127.0.0.1:8765` |
| `WORDGRAM_DECK` | target deck | `English::Vocabulary` |
| `WORDGRAM_ACCENT` | `us` or `uk`, used for dictionary audio choice and TTS voice | `us` |
| `WORDGRAM_TTS_VOICE` | edge-tts voice | `en-US-AriaNeural` (or `en-GB-SoniaNeural` when accent=uk) |
| `WORDGRAM_DATA_DIR` | queue DB + downloaded audio | `~/.wordgram` |

## Module layout

```
src/wordgram/
  __about__.py      # version (exists)
  __init__.py       # exists
  config.py         # Settings
  main.py           # entry point: build app, run polling; console_script `wordgram`
  bot.py            # handlers: whitelist filter, commands, word messages
  streaming.py      # placeholder-edit loop bridging agent stream -> Telegram edits
  agent.py          # subprocess runner yielding text deltas
  prompt.py         # prompt template + card-payload extraction
  card.py           # card dataclass, validation of the LLM payload
  anki.py           # AnkiConnect client (add note, dedup, model/deck bootstrap, delete)
  audio.py          # dictionary lookup + edge-tts fallback -> local mp3 path
  pending.py        # sqlite queue of cards not yet delivered to Anki + retry task
```

Add `wordgram = "wordgram.main:cli"` to `[project.scripts]`.

## The LLM contract (core of the system)

`prompt.py` builds one prompt per request:

```text
Ты помощник по изучению английской лексики. Тебе дано английское слово
или короткая фраза: {word}

Ответь по-русски, компактно, без вступлений и без завершающих фраз.
Структура ответа:

1. Первая строка: слово, транскрипция IPA, часть(и) речи.
2. Переводы — по убыванию частотности в обыденной речи, с пометами
   (разг., книжн., сленг, груб. и т.п.) там, где они важны.
3. Употребление: типичные сочетания и предлоги, с чем часто путают.
4. Происхождение: если слово заимствовано — 1-3 предложения о том, из
   какого языка и как пришло; для исконных слов — одна строка.
5. Примеры: 2-4 коротких предложения из повседневной жизни, каждое с
   переводом.

Если во входе похоже на опечатку — начни с «Возможно, вы имели в виду:
…» и разбирай исправленный вариант.
Если это идиома или фразовый глагол — объясни буквальный и переносный
смысл и типичные ситуации употребления.
Не используй markdown-разметку, только простой текст.
Весь разбор — не длиннее 3500 символов.

После разбора выведи строку ровно ===CARD=== и сразу за ней JSON в одну
строку без пояснений:
{"word": "...", "ipa": "...", "translations": ["...", "..."],
 "examples": [{"en": "...", "ru": "..."}]}
translations — 2-4 главных перевода; examples — 1-2 самых коротких
примера из разбора.
```

Parsing rules (`prompt.py` / `card.py`):

- Everything before `===CARD===` is the Telegram text. During streaming,
  cut the displayed text at the delimiter as soon as any prefix of it
  appears at the end of the buffer (never flash `===CA` to the user).
- After the run, parse the JSON after the delimiter into `Card`
  (`word`, `ipa`, `translations: list[str]` non-empty,
  `examples: list[Example]`). On any parse/validation failure: the
  Telegram answer still goes out, no card is created, status line says
  the card failed — never crash the handler.

## Milestones

Each milestone = one or more commits, tests included, CI green.

### M1 — config + bot skeleton

`config.py`, `main.py`, `bot.py`: application starts, long polling,
whitelist filter (non-whitelisted updates are ignored, only debug-logged),
`/start` and `/help` reply with static text. Input validation for word
messages per the functional description (Latin letters, spaces, hyphens,
apostrophes; max ~50 chars; otherwise a short hint). Handler for a valid
word replies with a stub. Tests: validation function, whitelist filter
(use PTB objects directly, no live bot).

### M2 — agent runner

`agent.py`: `async def stream_completion(prompt: str) -> AsyncIterator[str]`
— spawns the agent selected by `WORDGRAM_AGENT` from its command
template. Template handling: `shlex.split` the template FIRST, then
substitute `{model}` and `{prompt}` inside individual argv tokens with
`str.replace` — substitution after splitting means prompt content can
never break quoting; no shell is involved.

Two output parsers, chosen by agent:

- `stream-json` (claude): parse JSON-lines on stdout, yield text deltas
  from `stream_event`/`content_block_delta` events; if none arrived by
  process exit, fall back to the `result` event's full text as a single
  yield.
- `plain` (antigravity, gemini): yield decoded stdout chunks as they
  arrive. If the CLI buffers its output, the whole answer arrives as one
  late chunk — acceptable degradation, the streaming bridge (M3) handles
  it transparently.

Enforce `WORDGRAM_AGENT_TIMEOUT` (kill process, raise `AgentError`).
Non-zero exit or empty output → `AgentError` with stderr tail in the
message. Tests: fake agents = tiny Python scripts in `tests/` — a
stream-json one (happy path, no-deltas path, nonzero exit, hang for the
timeout path with a sub-second timeout) and a plain one (chunked output,
single-blob output); plus template rendering tests proving `{prompt}`
with quotes/spaces/newlines survives intact for every default template.

### M3 — streaming bridge

`streaming.py`: post placeholder "⏳ {word} …", accumulate deltas, edit
the message at most every 1.5 s and only when visible text changed
(remember: cut at delimiter, see LLM contract). Final edit with the
complete text; append the status line placeholder later (M5). On
`AgentError`: edit the message to a short apology + `/redo` hint.
Truncate visible text at 4000 chars with an ellipsis. Handle Telegram
`RetryAfter`/`BadRequest("message is not modified")` gracefully. Tests:
fake `Message.edit_text` recorder + scripted delta sequences; assert edit
cadence, delimiter cutting, truncation, error path.

### M4 — card extraction

`card.py` + prompt module: parse the payload per the LLM contract.
Tests: valid payload, payload with trailing garbage, missing delimiter,
malformed JSON, empty translations.

### M5 — Anki integration

`anki.py`, httpx-based AnkiConnect client (`version`, `createDeck`,
`modelNames`, `createModel`, `findNotes`, `addNote`, `deleteNotes`,
`storeMediaFile`). On startup (lazily, first use): ensure deck and note
type `Wordgram` exist. Note type fields: `Word`, `IPA`, `Translations`,
`Examples`, `Audio`; one card template — Front: `{{Word}} {{Audio}}`,
Back: `{{IPA}}<br>{{Translations}}<hr>{{Examples}}`; minimal CSS.
Duplicate check: `findNotes` with query `note:Wordgram "Word:{word}"`
(case-insensitive match is Anki's default). `add_card(card, audio_path)`
returns `added | duplicate`; audio is sent with `storeMediaFile`
(filename `wordgram-{slug}.mp3`) and referenced as `[sound:...]` in the
`Audio` field. Wire into the handler after the final edit: status line
appended to the message. Track the last added note id in memory for
`/undo` (M7). Tests: mock httpx transport; assert exact AnkiConnect
payloads for bootstrap, dedup, add-with-audio, and error propagation.

### M6 — pronunciation audio

`audio.py`: `async def fetch_pronunciation(word: str) -> Path | None`.
Try dictionaryapi.dev (accent preference, first non-empty audio URL,
download to `WORDGRAM_DATA_DIR/audio/`); on any failure or for
multi-word input, fall back to edge-tts with the configured voice; on
TTS failure return `None`. Runs via `asyncio.create_task` in parallel
with the agent stream; awaited only after the final edit. Send to chat
with `send_voice` (mp3 is accepted); if Telegram rejects it, fall back
to `send_audio`; if no audio, add "🔇 no audio" to the status line.
Tests: mocked httpx for the dictionary path (hit, miss, HTTP error),
monkeypatched edge-tts, phrase input goes straight to TTS.

### M7 — pending queue and remaining commands

`pending.py`: sqlite table `pending_cards(id, card_json, audio_path,
created_at)`. When `add_card` fails with a connection error, enqueue and
set status "🕓 card queued". Background task retries the whole queue
every 60 s; on success, edit nothing (cards just appear in Anki) but log.
Queue survives restarts (DB in `WORDGRAM_DATA_DIR`). Implement `/status`
(selected agent and model, Anki reachable yes/no, queue size), `/undo` (delete
last added note via `deleteNotes`, confirm), `/redo` (re-run last word
for this chat). Tests: enqueue on connection error, retry drains queue,
undo/redo state machine (per chat, in memory).

### M8 — polish and release

README: install (`uv tool install wordgram` / `uvx wordgram`), required
env vars, AnkiConnect setup pointer, systemd/launchd hint (one paragraph,
no unit files). Bump version to `0.1.0`. Ensure `ruff check` is clean and
wired into CI. Do NOT push the `v0.1.0` tag — publishing is deferred
until PyPI credentials are configured; note this in the README.

## Decisions pre-made for v0.1 (do not re-open)

- One combined card per word, even for multiple meanings.
- Duplicate send → report only, card untouched.
- No "lookup without Anki" escape hatch.
- Single fixed deck from config.
- Accent: config-level only (`WORDGRAM_ACCENT`), no per-message choice.
- Plain text in Telegram (no parse_mode) — formatting is a later
  iteration, not v0.1.
- One globally selected agent (`WORDGRAM_AGENT`) — no per-task routing
  tables like news-recap has; a single-user bot doesn't need them.

## Out of scope for v0.1

Webhooks, Docker, multiple users with separate decks, example-sentence
audio, spaced-repetition statistics, any web UI.
