# Analytics AI — implementation plan

See `specs/reference/analytics-ai.md` for architecture, storage design, LLM
strategy, invariants, and Analytics Views design.

## Remaining deliverables

### Template notebooks and extended dashboard

1. `notebooks/events.py` — event/trip cost breakdown notebook.
2. `notebooks/tags.py` — tag-bucket comparison notebook.
3. `dashboard.py` extended to full configurable widget set.

### MCP server extensions

4. `get_config(key)` and `set_config(key, value)` tools in `mcp_server.py`.

---

### AI Views feature

5. **`queries/spending_summary.sql`** — aggregates last-12-months expenses into
   three result sets: events (id, name, total_amount, date_from, date_to),
   tags (id, name, expense_count, total_amount), category groups (id, name,
   total_amount). Used by the LLM before proposing a new view.

6. **`queries/view_data.sql`** — given a basket config passed as a JSON parameter,
   assigns each expense to its first-matching basket (event match checked before
   tag match, unmatched → default basket name), then aggregates by
   (basket_name, year_month, group_name). Returns one row per
   (basket, month, group) triple.

7. **`settings.py` extensions** — `list_view_ids() → list[str]`, `get_view(id)`,
   `save_view(config: dict)`, `delete_view(id)`. Keys in LMDB: `view:<uuid>`.

8. **MCP server** — expose `list_views`, `get_view(id)`, `save_view(config)`,
   `delete_view(id)` tools so Claude Desktop / Claude Code can manage views
   externally.

9. **In-session LLM tools in `dashboard.py`** (Gemini chat tool definitions):
    - `query_spending_summary()` → runs `spending_summary.sql`, returns JSON
    - `propose_view(baskets, default_basket, chart_type)` → sets the in-memory
      draft view config and triggers chart re-render; does not save
    - `update_basket(name, event_ids, tag_ids)` → modifies a basket in the draft
    - `remove_basket(name)` → removes a basket from the draft
    - `set_chart_type(type)` → updates draft chart type
    - `save_current_view(name)` → persists draft via `settings.save_view()`
    - `delete_view(id)` → removes a saved view via `settings.delete_view()`

10. **Altair chart for basket views** — stacked bar: X = year_month, Y = amount,
    color = basket_name. On click of a bar segment: filter to that basket + period
    and show a secondary stacked bar by group_name as a drill-down panel below.
    Use `alt.selection_point` on basket + month for the drill-down interaction.

11. **Unified chat surface in `dashboard.py`** — a single conversation driven by Marimo
    state (`chat_history`), rendered as message bubbles, is the only AI area. Above it,
    a row of clickable suggestion buttons sends a starter prompt straight into the
    conversation (the obvious one rebuilds spending into baskets; others refine); a
    free-text box handles the rest. `mo.ui.chat` is not used because its `prompts=` are
    a slash-command menu, not visible buttons. A period selector (last 12 months / this
    year / last year) drives both the draft and the saved-view gallery.

12. **Chat system prompt** — instructs the LLM: (a) call `query_spending_summary()`
    first, (b) aim for 5–10 top-level baskets that reveal non-obvious actionable
    patterns — not obvious dominant items like rent; start from PWA category groups but
    reorganise freely, extracting cross-cutting baskets by event or tag (e.g. travel tag
    → one basket, relocation event → one basket), merging negligible items into the
    default basket; (c) call `propose_view()` with a concrete basket set so the draft
    chart renders live below the chat; (d) justify each basket with concrete numbers;
    (e) end each turn with 3–5 follow-up questions; (f) to persist on request, call
    `save_current_view(name)`; never ask the user to name categories or tags.

13. **Draft + pinned-views gallery** — the live draft renders below the chat with a Pin
    control (name field + button) that persists it via `settings.save_view()`. Pinned
    views render as a gallery of live cards (each re-executes `view_data.sql` for the
    selected period via `make_basket_chart`), with per-card "open in draft" and "delete"
    buttons.
