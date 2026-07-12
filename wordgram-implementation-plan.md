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
| TTS (primary) | Kokoro-82M via `kokoro-onnx` — local, Apache 2.0, near-natural English, faster than real time on CPU; nothing external to break. Model (~300 MB) downloaded by a background task at startup into `WORDGRAM_DATA_DIR/models/` (see M6). Verify at M6 whether `kokoro-onnx` needs the `espeak-ng` system library for phonemization — if it does, it is a documented system requirement, not a hidden crash |
| TTS (last resort) | `edge-tts` (MS Edge voices, free online, outputs mp3) — only when Kokoro fails; its known flakiness (unofficial API, recurring 403 breakage) is acceptable in this role |
| mp3 encoding | `lameenc` (pure-wheel LAME bindings) to convert Kokoro's WAV output to mp3 — no ffmpeg system dependency |
| Dictionary pronunciation | `https://api.dictionaryapi.dev/api/v2/entries/en/{word}` — take the first `phonetics[].audio` non-empty URL (they are Wiktionary recordings); prefer entries whose URL contains the configured accent (`-us` / `-uk`), else any |
| Settings | `pydantic-settings`, env prefix `WORDGRAM_`, `.env` support |
| Persistent queue | stdlib `sqlite3`, single DB file |
| LLM | CLI agent subprocess; the same three agents as news-recap — `claude` (default), `codex`, `antigravity` (the `agy` CLI running Gemini models); see Milestone 2 |
| Lint | `ruff` (line-length 99), run in CI after tests |

## Configuration (env vars)

| Variable | Meaning | Default |
|---|---|---|
| `WORDGRAM_BOT_TOKEN` | Telegram bot token | required |
| `WORDGRAM_ALLOWED_USER_IDS` | comma-separated Telegram user IDs | required |
| `WORDGRAM_AGENT` | `claude`, `codex`, or `antigravity` | `claude` |
| `WORDGRAM_CLAUDE_CMD` | claude argv template | `claude -p {prompt} --model {model} --output-format stream-json --include-partial-messages --verbose --disallowedTools "Bash,Edit,Write,NotebookEdit,Read,Glob,Grep,WebFetch,WebSearch,Task"` |
| `WORDGRAM_CODEX_CMD` | codex argv template | `codex exec --model {model} --sandbox read-only -c model_reasoning_effort=low --output-last-message {out_file} {prompt}` |
| `WORDGRAM_ANTIGRAVITY_CMD` | antigravity argv template | `agy --model {model} -p {prompt}` — never with `--dangerously-skip-permissions`; see "Agent hardening" |
| `WORDGRAM_MODEL` | model substituted into the template | per agent: `sonnet` (claude — nuanced Russian linguistic analysis is worth more than haiku's latency on a flat-rate plan), `gpt-5.2` (codex), `gemini-3.5-flash` (antigravity) |
| `WORDGRAM_AGENT_TIMEOUT` | seconds | `120` |
| `WORDGRAM_ANKI_URL` | AnkiConnect endpoint | `http://127.0.0.1:8765` |
| `WORDGRAM_DECK` | target deck | `English::Vocabulary` |
| `WORDGRAM_ACCENT` | `us` or `uk`, used for dictionary audio choice and TTS voices | `us` |
| `WORDGRAM_TTS_VOICE` | Kokoro voice | `af_heart` (us) / `bf_emma` (uk) |
| `WORDGRAM_EDGE_TTS_VOICE` | last-resort edge-tts voice | `en-US-AriaNeural` (us) / `en-GB-SoniaNeural` (uk) |
| `WORDGRAM_DATA_DIR` | queue DB + downloaded audio | `~/.wordgram` |

## Agent hardening

The bot forwards user text into a coding agent running with the user's
own account on the user's own laptop. Input validation (Latin letters,
~50 chars) is NOT a security boundary — "delete all files in home dir"
passes it. The agents therefore must not be able to act on the text at
all: no shell, no file access, no web tools. The default templates
above encode this — `--disallowedTools` for claude, `--sandbox
read-only` for codex, and no permission-skipping flag for antigravity.
M2 must verify by hand that each agent completes a plain-text
generation under these restrictions (exact flag names may need
adjusting to the installed CLI versions); if an agent cannot run
non-interactively without being granted tool permissions, drop it from
the supported list rather than run it unrestricted.

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
  card.py           # note dataclass (word, ipa, 1-3 meanings), validation of the LLM payload
  anki.py           # AnkiConnect client (add note, dedup, model/deck bootstrap, delete)
  audio.py          # dictionary lookup + TTS fallbacks -> local mp3 path
  pending.py        # sqlite queue of notes not yet delivered to Anki + retry task
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
Для выделения используй ТОЛЬКО HTML-теги <b> и <i>: разбираемое слово —
жирным, английские примеры — курсивом. Никакого markdown, никаких
других тегов.
Весь разбор — не длиннее 3500 символов.

После разбора выведи строку ровно ===CARD=== и сразу за ней JSON в одну
строку без пояснений и без HTML-тегов внутри значений:
{"word": "...", "ipa": "...",
 "meanings": [{"label": "...", "translations": ["...", "..."],
 "examples": [{"en": "...", "ru": "..."}]}]}
Обычно meanings содержит один элемент с пустым label. Раздели на
несколько (не более трёх) только если значения слова не связаны между
собой (как bank «банк» и bank «берег»); тогда label — помета в 1-3
русских слова, различающая значения. translations — 2-4 главных
перевода этого значения; examples — 1-2 самых коротких примера именно
этого значения.
```

Parsing rules (`prompt.py` / `card.py`):

- Everything before `===CARD===` is the Telegram text. During streaming,
  cut the displayed text at the delimiter as soon as any prefix of it
  appears at the end of the buffer (never flash `===CA` to the user).
- After the run, parse the JSON after the delimiter into a single
  `Note` (`word`, `ipa`, `meanings: list[Meaning]` where each meaning
  has `label` possibly empty, `translations: list[str]` non-empty,
  `examples: list[Example]`); reject an empty meanings list or more
  than three meanings. On any parse/validation failure: the Telegram
  answer still goes out, no note is created, status line says the card
  failed — never crash the handler.

HTML safety (`streaming.py`): Telegram gets `parse_mode=HTML`, and the
LLM is only *asked* to emit `<b>`/`<i>` — the code must enforce it.
Sanitizer over the visible text: escape `&`, `<`, `>` everywhere except
whitelisted `<b>`, `</b>`, `<i>`, `</i>` tags; auto-close tags left
open at the current cut point (streaming can split a tag pair across
edits). If Telegram still rejects an edit with an entity-parse error,
retry that edit without `parse_mode` — degraded but delivered.

## Milestones

Each milestone = one or more commits, tests included, CI green.

### M1 — config + bot skeleton

`config.py`, `main.py`, `bot.py`: application starts, long polling,
whitelist filter (non-whitelisted updates are ignored, only debug-logged),
`/start` and `/help` reply with static text (help mentions the `?`
prefix). Input validation for word messages per the functional
description (Latin letters including accented ones — café, naïve —
plus spaces, hyphens, apostrophes; max ~50 chars; otherwise a short
hint). A leading `?` (with optional space)
marks the request lookup-only and is stripped before validation.
Handler for a valid word replies with a stub. Tests: validation
function including the `?` prefix, whitelist filter (use PTB objects
directly, no live bot).

### M2 — agent runner

`agent.py`: `async def stream_completion(prompt: str) -> AsyncIterator[str]`
— spawns the agent selected by `WORDGRAM_AGENT` from its command
template. Template handling: `shlex.split` the template FIRST, then
substitute `{model}`, `{prompt}`, and `{out_file}` inside individual
argv tokens with `str.replace` — substitution after splitting means
prompt content can never break quoting; no shell is involved.
`{out_file}` is a temp file path the runner always provides (only the
codex template uses it).

Three output parsers, chosen by agent:

- `stream-json` (claude): parse JSON-lines on stdout, yield text deltas
  from `stream_event`/`content_block_delta` events; if none arrived by
  process exit, fall back to the `result` event's full text as a single
  yield.
- `last-message` (codex): stdout carries codex's session header and
  reasoning noise, so it is ignored for content; after a zero exit, read
  the answer from `{out_file}` and yield it once. No incremental
  streaming for codex.
- `plain` (antigravity): yield decoded stdout chunks as they arrive. If
  the CLI buffers its output, the whole answer arrives as one late
  chunk — acceptable degradation, the streaming bridge (M3) handles it
  transparently.

Enforce `WORDGRAM_AGENT_TIMEOUT` (kill process, raise `AgentError`).
Non-zero exit, empty output, or missing/empty `{out_file}` →
`AgentError` with stderr tail in the message. Tests: fake agents = tiny
Python scripts in `tests/` — a stream-json one (happy path, no-deltas
path, nonzero exit, hang for the timeout path with a sub-second
timeout), a last-message one (writes the out file; also the
missing-out-file failure), and a plain one (chunked output, single-blob
output); plus template rendering tests proving `{prompt}` with
quotes/spaces/newlines survives intact for every default template.

Closing this milestone requires the manual check from "Agent
hardening": run each real CLI once with its default template and
confirm the answer arrives with tools disabled. This is the only
sanctioned manual step in the plan — real CLIs stay out of the test
suite.

### M3 — streaming bridge

`streaming.py`: post placeholder "⏳ {word} …", accumulate deltas, edit
the message at most every 1.5 s and only when visible text changed
(remember: cut at delimiter, see LLM contract), passing every edit
through the HTML sanitizer (see LLM contract) with `parse_mode=HTML`.
Final edit with the complete text; append the status line placeholder
later (M5). On `AgentError`: edit the message to a short apology +
`/redo` hint. Truncate visible text at 4000 chars with an ellipsis.
Handle Telegram `RetryAfter`/`BadRequest("message is not modified")`
gracefully; entity-parse `BadRequest` → retry the edit without
`parse_mode`.

Word messages are processed strictly one at a time — a single global
`asyncio.Lock` around the whole word pipeline. After downtime Telegram
delivers up to 24 h of backlog in one burst; without the lock that
means parallel agent subprocesses and interleaved edit loops flooding
Telegram's rate limits. Each queued word still gets its own
placeholder immediately, so the user sees the backlog was accepted.

Tests: fake `Message.edit_text` recorder + scripted delta sequences;
assert edit cadence, delimiter cutting, truncation, error path;
sanitizer cases — stray `<`/`&`, disallowed tags escaped, `<b>` split
across two deltas, unclosed `<i>` auto-closed at the cut; two words
sent together → second agent run starts only after the first pipeline
finishes.

### M4 — card extraction

`card.py` + prompt module: parse the payload per the LLM contract.
Tests: valid single-meaning payload, valid multi-meaning payload (2-3
meanings with labels), payload with trailing garbage, missing
delimiter, malformed JSON, empty translations, empty meanings list,
four meanings (rejected).

### M5 — Anki integration

`anki.py`, httpx-based AnkiConnect client (`version`, `createDeck`,
`modelNames`, `createModel`, `findNotes`, `addNote`, `deleteNotes`,
`storeMediaFile`). On startup (lazily, first use): ensure deck and note
type `Wordgram` exist. Note type fields: `Word`, `IPA`, `Meanings`,
`Audio`; one card template — Front: `{{Word}} {{Audio}}<br>{{IPA}}`
(the functional description puts IPA on the front — it describes the
word's form, not the answer), Back: `{{Meanings}}`; minimal CSS. The
`Meanings` field is rendered by the backend from the parsed payload:
one block per meaning — label in bold (only when the note has more
than one meaning), translations on one line, examples in italics with
translations — numbered `<ol>`-style when there is more than one
block.

One word = one note, so the first field (`Word`) is naturally unique
and Anki's own `addNote` duplicate rejection never fires against our
own notes. Duplicate check before adding: `findNotes` with query
`note:Wordgram "Word:{word}"` (case-insensitive match is Anki's
default) — if a note exists, nothing is added and the send reports
duplicate. Belt and braces: an `addNote` "duplicate" error (a note
added by hand between check and add) is treated as the duplicate
status, not as a failure.

`add_note(note, audio_path)` returns `added(note_id) | duplicate`;
audio is sent once with `storeMediaFile` (filename
`wordgram-{slug}.mp3`) and referenced as `[sound:...]` in the `Audio`
field. Skipped entirely for lookup-only (`?`) requests — status line
"👁 lookup only". Wire into the handler after the final edit: status
line appended to the message. Track the last added note id in memory
for `/undo` and `/redo` (M7). Tests: mock httpx transport; assert
exact AnkiConnect payloads for bootstrap, dedup, single- and
multi-meaning rendering of the `Meanings` field, audio reference,
lookup-only skip, addNote-duplicate-error → duplicate status, and
error propagation.

### M6 — pronunciation audio

`audio.py`: `async def fetch_pronunciation(word: str) -> Path | None`,
a three-step chain where each step falls through to the next on ANY
exception (log at warning level, never raise):

1. **Dictionary recording** — dictionaryapi.dev (accent preference,
   first non-empty audio URL, download mp3 to
   `WORDGRAM_DATA_DIR/audio/`). Skipped for multi-word input.
2. **Kokoro (local TTS)** — `kokoro-onnx` with the configured voice.
   `kokoro-v1.0.onnx` + `voices-v1.0.bin` are downloaded into
   `WORDGRAM_DATA_DIR/models/` by a background task started at bot
   startup — NOT on first request, where the ~300 MB download would
   delay the first voice message by minutes (log progress; a failed
   download must not corrupt the cache — download to a temp name,
   rename on success; retry on next startup). Until the files are in
   place this step reports "not ready" and the chain falls through to
   step 3. Run inference in `asyncio.to_thread` (it is CPU-bound).
   Encode the returned samples to mp3 with `lameenc`. Import
   `kokoro_onnx` lazily at call time so a broken install degrades to
   step 3 instead of killing the bot at startup — this is the one
   sanctioned exception to the top-level-imports rule; mark it with a
   comment. First task of this milestone: check on a clean machine
   whether `kokoro-onnx` phonemization needs the `espeak-ng` system
   library; if yes, document it in the README (M8) as an optional
   system requirement — without it Kokoro falls through to edge-tts.
3. **edge-tts (online, last resort)** — `WORDGRAM_EDGE_TTS_VOICE`,
   native mp3 output. On failure return `None`.

Runs via `asyncio.create_task` in parallel with the agent stream;
awaited only after the final edit. Send to chat with `send_voice` (mp3
is accepted); if Telegram rejects it, fall back to `send_audio`; if no
audio, add "🔇 no audio" to the status line. Tests: mocked httpx for
the dictionary path (hit, miss, HTTP error); fake kokoro module
(success, import failure, inference failure) asserting fall-through
order; monkeypatched edge-tts (success, failure → `None`); phrase input
skips the dictionary step.

### M7 — pending queue, stats, and remaining commands

`pending.py`: sqlite (DB in `WORDGRAM_DATA_DIR`, survives restarts)
with two tables:

- `pending_notes(id, word, note_json, audio_path, created_at)` — when
  `add_note` fails with a connection error, enqueue the note and set
  status "🕓 card queued". Background task retries the queue every
  60 s; before each delivery it re-runs the duplicate check
  (`findNotes`) and silently drops the entry on a hit — the note may
  have been added by hand or by an earlier entry while Anki was down.
  On success, edit nothing (the card just appears in Anki) but log.
- `word_log(id, word, meanings_count, action, created_at)` where action
  is `added | duplicate | lookup`, written on every processed word —
  the source for `/stats`.

The handler's duplicate check (M5) is extended here: a word counts as
duplicate if a note exists in Anki OR a `pending_notes` entry for the
same word is waiting — otherwise re-sending a word while Anki is down
would enqueue it twice and both copies would land after the drain.

Commands:

- `/status` — selected agent and model, Anki reachable yes/no, queue
  size.
- `/stats` — words added today / last 7 days / all time, plus
  duplicates and lookups counts.
- `/undo` — remove whatever the last sent word produced: delete its
  note via `deleteNotes` if it reached Anki, or delete its
  `pending_notes` row if it is still queued; confirm with the word
  name.
- `/redo` — re-run the last word for this chat, preserving its
  lookup-only flag. Before adding the new note, remove the previous
  run's result exactly like `/undo` does — `/redo` exists to fix a
  poor generation, and without the removal the duplicate check would
  block the replacement ("already in Anki") and the bad card would
  survive.

Undo/redo state (last word, its note id or pending row id, lookup
flag) is per chat, in memory, lost on restart — documented behavior.
Tests: enqueue on connection error, retry drains queue, retry re-checks
duplicates and drops the entry, handler dedup consults the queue, stats
aggregation windows, undo removes an added note, undo removes a queued
row, redo replaces the previous note, undo/redo state machine.

### M8 — polish and release

README: install (`uv tool install wordgram` / `uvx wordgram`), required
env vars, AnkiConnect setup pointer, systemd/launchd hint (one paragraph,
no unit files). Bump version to `0.1.0`. Ensure `ruff check` is clean and
wired into CI. Do NOT push the `v0.1.0` tag — publishing is deferred
until PyPI credentials are configured; note this in the README.

## Product decisions (all questions resolved — do not re-open)

- One note per word. Genuinely unrelated meanings (bank «банк» / bank
  «берег») become numbered blocks on the back — at most three, split
  by the LLM; usually one. Never separate cards: identical fronts
  would be indistinguishable during review, and one note per word
  keeps dedup, undo, and the queue trivially correct.
- Duplicate send → report only ("📌 already in Anki"), existing note
  untouched. The duplicate check covers the pending queue too.
- `?` prefix = lookup-only: analysis and audio, no Anki card.
- `/undo` covers queued notes; `/redo` replaces the previous run's
  note instead of being blocked by the duplicate check.
- Single fixed deck from config, no switching.
- Accent: config-level only (`WORDGRAM_ACCENT`), US default, one
  recording per card, no per-message choice.
- Telegram formatting IS in v0.1: HTML `<b>`/`<i>` only, enforced by
  the sanitizer, plain-text fallback on parse errors.
- Word/phrase audio only — example sentences are never voiced (final).
- `/stats` IS in v0.1 (see M7).
- One globally selected agent (`WORDGRAM_AGENT`) — no per-task routing
  tables like news-recap has; a single-user bot doesn't need them.
- Agents run with tool execution disabled (see "Agent hardening") —
  not optional, an agent that can't be restricted is dropped.
- Words are processed sequentially (global lock), so a 24 h Telegram
  backlog drains one word at a time.

## Out of scope — final, not deferred

Webhooks, Docker, multiple users with separate decks, example-sentence
audio, any web UI.
