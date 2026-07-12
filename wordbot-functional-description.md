# Telegram → Anki Word Bot — Functional Description

> **Draft.** This document describes a new standalone project (name TBD).
> It is parked in the `dinary` repository only because the design session
> started here; it will move to the new project's repository once created.

## Purpose

A personal vocabulary assistant. The user sends an English word or short
phrase to a Telegram bot and gets back, within seconds, a rich explanation
in Russian: translations, usage, origin, examples. At the same time a
compact flashcard is added automatically to the user's local Anki
collection, so every word looked up during the day becomes review material
with zero extra effort.

## System context

- **Telegram bot** — the only user interface. Personal use: a whitelist of
  Telegram user IDs; messages from anyone else are ignored silently.
- **Backend** — a single service running on the user's laptop, the same
  machine that runs Anki. It connects to Telegram via long polling, so no
  public IP, domain, or webhook is needed.
- **LLM** — a CLI coding agent under a flat-rate subscription
  (`claude -p`, with the agent abstraction kept open for `codex` and
  others, following the approach proven in `news-recap`). No per-token
  API cost. An API backend remains a possible fallback for speed
  experiments.
- **Anki** — Anki desktop with the AnkiConnect add-on, reachable from the
  backend on localhost.

## Core flow

1. The user sends an English word or short phrase (idiom, phrasal verb,
   collocation — anything that makes a valid flashcard).
2. The bot validates the input: Latin script, length within a small limit,
   not a command. Anything else gets a short hint instead of an LLM call.
3. The bot immediately posts a placeholder message ("⏳ *word* …") so the
   user sees the request was accepted.
4. The LLM agent runs in streaming mode. As text arrives, the bot edits
   the placeholder in place, throttled to Telegram's edit rate limits
   (roughly one edit per 1–2 seconds). The first translations are visible
   within a few seconds; the full answer completes without a second
   message.
5. The LLM produces **both outputs in one generation**: the full
   explanation for Telegram and, after a machine-readable delimiter, a
   compact card payload. The card block is stripped from the streamed
   message and never shown to the user.
6. When generation completes, the backend adds the note to Anki via
   AnkiConnect and appends a status line to the same Telegram message:
   "✅ added to Anki" / "📌 already in Anki" / "🕓 Anki is not running —
   card queued".

## Analysis content (the Telegram answer)

The prompt asks the LLM for, in this order:

- **Translations to Russian**, ordered by likelihood in everyday speech,
  each marked with part of speech and register (neutral / colloquial /
  formal / slang) where it matters.
- **IPA transcription.**
- **Usage notes**: typical collocations and prepositions, common
  confusions with similar words, countability/irregular forms when
  relevant.
- **Origin**: if the word was borrowed into English from another language,
  a short story of where it came from and how it traveled; otherwise a
  one-line note on origin. No forced etymology essays for native words.
- **Examples**: 2–4 short sentences from everyday contexts, each with a
  Russian translation.

Additional behavior:

- If the input looks misspelled, the answer starts with the suggested
  correction and analyzes the corrected word, clearly marked.
- For idioms and phrasal verbs: the meaning, literal vs figurative sense,
  and typical situations where it is used.
- The whole answer must fit in one Telegram message (4096 chars), so the
  prompt enforces a compact style.

## Anki card

- **Compact by design.** Front: the word/phrase with IPA transcription.
  Back: the top 2–4 translations plus 1–2 short examples. The long-form
  analysis (etymology, full meaning list) stays in Telegram only — cards
  must remain quick to review.
- **Deck and note type are configurable**; single deck by default
  (e.g. `English::Vocabulary`).
- **Duplicates**: if a note for the same word already exists, no second
  card is created; the bot reports "already in Anki".
- **Anki unavailable** (application closed, laptop just woke up): the card
  goes into a persistent local queue and is retried until AnkiConnect
  responds; the user is told the card is queued. The Telegram answer is
  never delayed by Anki problems.

## Bot commands

Kept minimal:

- `/start`, `/help` — what the bot does, how to use it.
- `/status` — agent health, Anki reachability, pending card queue size.
- `/undo` — remove the most recently added card (mistaken sends).
- `/redo` — re-run the analysis for the last word (e.g. after a poor
  generation).

Everything else is plain text input.

## Non-functional requirements

- **Latency**: first visible content within ~3–5 s; complete answer
  within ~20–30 s; card added within ~5 s after generation ends.
- **Cost**: LLM usage rides the existing coding-agent subscription; the
  design must not require a metered API key.
- **Resilience**: the laptop is not always on. Telegram keeps undelivered
  updates for 24 h, so words sent while the backend is down are processed
  when it starts. The Anki queue survives restarts.
- **Single instance, single user** (whitelist may hold a few family IDs).
  No horizontal scaling concerns.

## Open questions

1. **Project name** — to be chosen iteratively (separate step).
2. Multiple distinct meanings of one word: one combined card (current
   assumption) or several cards, one per meaning?
3. Duplicate word sent again: just report, or offer to update/extend the
   existing card?
4. An escape hatch to look a word up *without* adding it to Anki
   (e.g. message prefix `?`) — needed or noise?
5. Pronunciation audio (TTS) on the card — future enhancement?
6. Should decks be switchable per message/session (e.g. separate deck for
   idioms)?
