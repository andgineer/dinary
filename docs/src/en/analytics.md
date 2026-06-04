# Your Personal Financial Analyst

```bash
inv analytics
```

Opens a browser page at `http://localhost:2718` where you can **chat with your own spending data** in plain language — and browse interactive charts while you do it.

Ask things like:

- *"What did I spend most on last month?"*
- *"How does my food spending compare to last year?"*
- *"How much did the Italy trip cost, broken down by category?"*
- *"What's my savings rate for 2025?"*

The analyst knows your full expense history, categories, events, and tags. It queries your local database live — nothing leaves your machine.

## Charts

Alongside the chat, four visual summaries give you the big picture at a glance:

| | What it shows |
|---|---|
| **12-month rolling** | Top-10 categories stacked by month, with income and monthly savings |
| **Year comparison** | Any previous year overlaid on the rolling view |
| **Event** | Where the money went during a trip or project |
| **Tag** | Spending pattern for a label (e.g. "work", "dog") across a chosen year |

## Setup

The chat requires a Gemini API key configured as a provider in `.deploy/llm_providers.toml`. Without it, the charts still work — only the chat shows a warning.

The **year comparison**, **tag**, and **tag year** selectors remember your last choice across restarts. The **event** selector always opens on the most recent completed event.
