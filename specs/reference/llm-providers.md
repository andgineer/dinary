# Free LLM Providers — Receipt Classification Research

> Tested 2026-05-07/08 against 6 real Serbian fiscal receipts.
> Task: classify receipt items into Russian expense categories via a single batch JSON prompt.

---

## Test Methodology

- **Model**: one LLM call per receipt, all items in one batch prompt
- **Categories**: Russian-language taxonomy (Еда: еда, Еда: фрукты, Еда: деликатесы, Семья и личное: одежда, etc.)
- **Item language**: Serbian (Latin and occasionally Cyrillic), with store-specific barcode suffixes
- **Key quality signals**: correct non-food classification (clothing), delistkategorie for premium food, confidence level, total match

---

## Provider Results

### ✅ Groq — `llama-3.3-70b-versatile`

| Attribute | Value |
|---|---|
| Base URL | `https://api.groq.com/openai/v1` |
| Free tier | ~14,400 req/day, 30 RPM |
| Key source | console.groq.com |
| Latency | Very fast (~1s) |

**Classification quality**: best overall. conf=4 on all items across all test receipts. Correctly classified clothing (`Muške nazuvice` → `Семья и личное: одежда`), produce, dairy, deli. Total always matched.

**Verdict**: **primary provider**.

---

### ✅ OpenRouter — `openai/gpt-oss-120b:free`

| Attribute | Value |
|---|---|
| Base URL | `https://openrouter.ai/api/v1` |
| Free tier | varies by model; gpt-oss-120b:free available without billing |
| Key source | openrouter.ai |
| Latency | Moderate (~3-5s) |

**Classification quality**: good. Protein bar (`Karamel čoko prot.čok.`) rated conf=3 instead of 4 — reasonable uncertainty for an ambiguous item. Clothing correctly classified. Total always matched.

**Verdict**: **first fallback**.

---

### ✅ OpenRouter — `nvidia/nemotron-3-super-120b-a12b:free`

| Attribute | Value |
|---|---|
| Base URL | `https://openrouter.ai/api/v1` |
| Free tier | free tier available |
| Key source | openrouter.ai (same key as above) |
| Latency | Moderate |

**Classification quality**: good. Deli meat (`Mesnata slanina`) and protein bar rated conf=3 — conservative but acceptable. Clothing correctly classified. Total always matched.

**Verdict**: **second fallback**.

---

### ✅ Google Gemini — `gemini-2.5-flash`

| Attribute | Value |
|---|---|
| Base URL | `https://generativelanguage.googleapis.com/v1beta/openai` |
| Free tier | 20 RPM, ~1500 req/day |
| Key source | **aistudio.google.com only** (keys from Google Cloud Console get limit=0) |
| Latency | Moderate, occasional 503 |

**Classification quality**: good when it responds. Same quality as Groq. Intermittent 503 errors observed during testing (model overload).

**Caveat**: `gemini-2.0-flash` has free tier quota = 0; only `gemini-2.5-flash` and newer work on free tier.

**Verdict**: **third fallback**. Lower priority due to 503 instability and 20 RPM cap.

---

### ❌ Cerebras — `llama3.1-8b` and `qwen-3-235b-a22b-instruct-2507`

**Removed.** `llama3.1-8b` failed to classify `Muške nazuvice` (Serbian for men's socks) — returned Unclassified, causing CLASSIFIED TOTAL ≠ RECEIPT TOTAL. `qwen-3-235b` hit 429 on the first request. Neither model is suitable.

---

## OpenRouter Free Models Tested (smoke test only)

| Model | Smoke test |
|---|---|
| `google/gemma-4-31b-it:free` | ❌ Provider error |
| `qwen/qwen3-next-80b-a3b-instruct:free` | ❌ Provider error |
| `mistralai/mistral-7b-instruct:free` | ❌ No endpoints found |
| `meta-llama/llama-3.1-8b-instruct:free` | ❌ No endpoints found |
| `openai/gpt-oss-120b:free` | ✅ |
| `nvidia/nemotron-3-super-120b-a12b:free` | ✅ |

---

## Seeded Provider Pool (priority order)

Stored in `.deploy/.env` as `DINARY_LLM_{n}_*` vars. Seeded into `llm_providers` table on first `init_db`.

| Priority | Provider | Model | Notes |
|---|---|---|---|
| 1 | Groq | llama-3.3-70b-versatile | Primary — best quality + speed |
| 2 | OpenRouter | openai/gpt-oss-120b:free | First fallback |
| 3 | OpenRouter | nvidia/nemotron-3-super-120b-a12b:free | Second fallback |
| 4 | Gemini | gemini-2.5-flash | Third fallback — 20 RPM cap |

Two OpenRouter entries use the same API key but different models — they share a rate limit bucket. If both hit 429 simultaneously, Gemini provides genuine independence.

---

## Failover Strategy

**Round-robin on 429/503**: move immediately to the next provider without waiting. The drain processes one job per iteration (300s default interval), so at steady state the pool is never exhausted. Last-used provider index stored in `app_metadata.llm_last_provider_idx` so load distributes evenly across iterations.

---

## Prompt Design Notes

- Single batch call per receipt: all normalized item names + store name + full active category list
- System prompt instructs JSON-only response: `[{"item": "...", "category_id": <int|null>, "confidence": <1-4>}]`
- On parse error (malformed JSON): fall back to `confidence_level=1` for all items
- Category list passed as `id: group_name: category_name` — the group context helps models understand that "фрукты" covers produce/vegetables, not just fruit

---

## Classification Observations from Real Receipts

- **Lidl barcode suffixes** (`/KOM/0082275`) do not confuse any model once stripped by the normaliser
- **"фрукты" as catch-all produce**: no dedicated vegetables category exists; models correctly infer produce items should go there
- **Premium food** (Burata, Gorgonzola): classified as `Еда: деликатесы` by most models, `Еда: еда` by Groq — both defensible given the category set
- **Non-food at Lidl** (`Muške nazuvice` = men's socks): only Groq, OpenRouter GPT-OSS and Nemotron classified correctly; Cerebras failed entirely
- **Serbian vocabulary** is handled well by all working models — no Serbian-specific fine-tuning needed
