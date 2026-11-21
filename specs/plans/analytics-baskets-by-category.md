# Plan: baskets by category and category group, and LLM drill-down

## Why

A basket matches an expense only by event or by tag. Over the last 12 months 35% of the
maintainer's spend carried neither (measured on the replica, 2026-11-12), so that third of the
money always lands in the default basket and no conversation with the analyst can split it. The
analyst cannot make "Groceries", "Rent" or "Cafés" baskets at all.

## Target behaviour

- A basket can also collect expenses by **category** and by **category group**. The analyst
  starts from the category groups as a baseline and reorganises freely: splits a group into its
  categories, merges negligible ones, extracts a cross-cutting basket by tag or event.
- **Drill-down.** From any basket of the draft the user asks to break it down. The analyst
  proposes a second-level set of 5–10 baskets that covers only that basket's expenses, by
  category, tag or event, with the same data-first methodology. The user can go back to the
  parent view.

## Design

### Triggers

`triggers` gains two lists next to `events` and `tags`: `categories` (category IDs) and
`category_groups` (group IDs). Matching stays "any trigger matches" inside a basket and
first-match across baskets, so a cross-cutting basket ("Travel" by tag) placed before a
category basket ("Food") takes the travel meals — the order of baskets is the analyst's tool
for that, and the prompt must say so.

Categories are matched by ID, never by name: `category-templates.md` requires analytics to
resolve a category by identity regardless of the active template or a rename. A group trigger
matches through the category's current group, so moving a category between groups moves its
history between group baskets; a view that must not shift pins categories instead.

Saved views without the new keys keep working: a missing list is an empty list.

### Drill-down

A view config gains an optional `scope`: one basket definition (name + triggers) taken from the
parent view. When present, only expenses matching `scope` are bucketed; everything else is
excluded, not sent to the default basket. First-match of the *parent* view must be respected,
otherwise a drill-down into "Food" would re-include the travel meals that "Travel" took. So
`scope` carries the parent's baskets that precede the drilled one as `excluded` triggers:
an expense is in scope when it matches the drilled basket and none of the preceding ones.

Drilling into the **default** basket is the most valuable case (it is the 35%): its scope is
"matches none of the parent's baskets".

The draft keeps a stack of views in page state, so "back" restores the parent draft. Pinning a
drilled draft saves it as an ordinary view with its scope; the gallery card shows the scope's
name in the title ("Food ▸ by category").

### Open points, with the recommended answer

- **Should the fixed category-group breakdown under the chart stay?** Yes. It is free and
  answers the common question without a model call; drill-down is for when it is not enough.
- **Should drill-down nest deeper than two levels?** No limit in the data model (scope composes),
  but the prompt asks for one level at a time and the UI shows a single "back".

## Changes

| File | Change |
|---|---|
| `src/dinary_analytics/queries/view_data.sql` | read `triggers.categories` / `triggers.category_groups`; apply `scope` (drilled basket, excluded preceding baskets, or "none of the parent's" for the default basket) |
| `src/dinary_analytics/queries/spending_summary.sql` | add per-category totals nested under each group, and a top-level `uncovered` figure: amount and share of spend with no event and no tag |
| `src/dinary_analytics/views.py` | nothing structural; the config is passed through as JSON |
| `src/dinary_analytics/notebooks/dashboard.py` | prompt: baskets by category/group, ordering rule, drill-down tool. New tool `drill_into(basket_name, baskets, default_basket)` that builds the scoped draft and pushes the parent on the stack; new tool or button `back_to_parent`. A "Break down" control per basket under the draft chart that sends the request into the conversation |
| `src/dinary_analytics/charts.py` | title shows the scope name when present |
| `src/dinary_analytics/ai_service.py` | none — views pass through unchanged; an MCP client can save a view with the new triggers |

`_propose_view` takes `baskets: list[dict]`; the tool schema generated in `llm.py` describes
that as an array of untyped items. The trigger shape is taught through the prompt today and
that stays, but the tool docstring must spell out all four trigger lists.

## Tests

- `tests/analytics/test_queries.py`: category trigger; group trigger; first-match between a
  tag basket and a category basket in both orders; a view without the new keys; scope on a
  named basket excludes expenses taken by a preceding basket; scope on the default basket;
  totals of a drilled view add up to the parent basket's total; `uncovered` in the summary.
- `tests/analytics/test_views.py`: frame for a scoped config.
- `tests/analytics/test_dashboard.py`: `drill_into` pushes the parent and sets the scoped draft;
  `back_to_parent` restores it; the "Break down" control sends a message; pin keeps the scope.

## When done

Update `specs/reference/analytics-ai.md`: the basket design goal (the analyst starts from
category groups and reorganises), the trigger list in "Basket structure", and a drill-down
paragraph. Update the charts table in `docs/src/en/analytics.md` and `docs/src/ru/analytics.md`
if the user-visible wording changes.
