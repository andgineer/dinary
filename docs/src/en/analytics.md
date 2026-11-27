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
- *"How much did I spend at Lidl this year, across all its shops?"*
- *"How much did coffee beans cost me this year?"*

The analyst knows your full expense history, categories, events and tags, and the stores and items of your scanned receipts. It queries a copy of your ledger kept on your own machine. To answer, it sends your question and the query results it needs to the AI model's provider (OpenAI); the charts work without sending anything.

## Charts

Alongside the chat, four visual summaries give you the big picture at a glance:

| | What it shows |
|---|---|
| **12-month rolling** | Top-10 categories stacked by month, with income and monthly savings |
| **Year comparison** | Any previous year overlaid on the rolling view |
| **Event** | Where the money went during a trip or project |
| **Tag** | Spending pattern for a label (e.g. "work", "dog") across a chosen year |

## Baskets

Ask the analyst to rebuild your spending into baskets and it proposes a chart of 5–10 baskets made from your categories, category groups, trips and tags, explaining each with its amount and share. Under the chart every basket has a **Break down** button: press it, and the analyst splits just that basket into finer baskets, for example "Food" by category or the catch-all basket by what is actually in it. **Back to parent view** returns to the chart you came from. Pin any chart, a breakdown included, to keep it; a pinned breakdown is titled after the basket it splits, such as "Food ▸ by category".

## Setup

The chat requires `OPENAI_API_KEY` in `.deploy/.env` — it runs on one paid OpenAI model, not the free providers the receipt pipeline uses. Without the key, the charts still work — only the chat shows a warning.

The **year comparison**, **tag**, and **tag year** selectors remember your last choice across restarts. The **event** selector always opens on the most recent completed event.
