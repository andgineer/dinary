# Project Description

## Current Setup

Personal budget is maintained in a Google Sheets spreadsheet (exported copy attached as reference).
The system is built around a two-level categorization: **categories** (specific expense types) and **envelopes** (broader budget buckets).

### Data Entry (main sheet, one per year)

Each month is represented as a repeating block of ~33 rows — one row per category.
All months within a year live on a single sheet.
The category list is identical every month and is created manually by duplicating the previous month's block.

Each row contains:

- **Date** (first of the month — used to group rows into months)
- **Amount in RSD** (Serbian dinars) — the receipt total, manually entered. Multiple purchases in the same category within a month are summed into a single cell (running total).
- **Amount in EUR** — auto-calculated from RSD using a fixed exchange rate
- **Category** — e.g., еда&бытовые (food & household), медицина (medicine), аренда (rent), топливо (fuel), кафе, гаджеты, булавки (partner's personal budget), etc.
- **Envelope** — a higher-level grouping, e.g., здоровье (health), путешествия (travel), релокация (relocation), ребенок (child), приложения (apps/subscriptions), профессиональное (professional), личный уход (personal care). Some categories have no envelope.
- **Comment** — free-text notes (e.g., "Замена резины", "Youtube Civilization VII busuu")
- **Columns for EUR and BAM** — occasionally used for expenses in foreign currencies during travel

### Aggregation (pivot-style sheets)

- **Расходы (Expenses)** — a pivot table: categories as rows, months as columns, values in EUR. Includes a Grand Total column.
- **Bottom summary row** compares: total expenses (Трошкови), income (Плата), and savings (Штедња = income − expenses) per month and YTD.
- **РасходыКонверт / КонвертРасходы** — intended pivot views by envelope (currently empty in the export, likely populated via Google Sheets pivot or formulas in the live version).

### Income (separate sheet)

- One row per pay date with salary in RSD and EUR.
- Rows pre-created for the full year (empty/zero for future months).

### Travel expenses

Some categories are duplicated with the envelope "путешествия" (travel) — e.g., "кафе / путешествия",
"еда&бытовые / путешествия", "аренда / путешествия", "транспорт / путешествия".
This allows separating everyday spending from travel spending in the same category.
Vacation purchases (often at higher prices) are tracked separately this way.

### What works well

- Simple, transparent, fully under personal control.
- The envelope system provides a meaningful high-level view of spending priorities.
- Monthly income vs. expenses vs. savings is immediately visible.
- EUR conversion gives a stable reference currency (RSD fluctuates).
- The travel-vs-everyday split is a useful distinction.

---

## Pain Points

### 1. Manual monthly setup

Every month, the full list of ~33 category rows must be manually duplicated and the date updated.
This is tedious and error-prone (rows can be accidentally skipped or misordered).

### 2. Supermarket receipts are opaque

A single supermarket receipt contains items that belong to different categories:
regular food, optional delicacies/treats, fruits, household chemicals, small appliances, pet supplies, etc.
Currently the entire receipt total goes into "еда&бытовые" — losing all granularity.

Serbian fiscal receipts contain a QR code linking to the tax authority website (SUF PURS) where individual line items are available.
This data could be parsed to split receipts into sub-categories automatically.

### 3. No item-level data

The system tracks receipt-level totals only.
There is no record of individual items purchased, which makes it impossible to answer questions like "how much did I spend on coffee beans this year"
or "what's the trend in fruit prices."

### 4. Category/envelope changes require retroactive manual work

If a category is renamed, split, or moved to a different envelope, all historical rows must be manually updated.
There is no separation between raw data and classification rules.

### 5. Limited analytical capability

Google Sheets provides basic pivot tables but no easy way to:
- Drill down into specific time ranges across years
- Compare arbitrary periods (e.g., Q1 2025 vs Q1 2026)
- Visualize trends, seasonality, or anomalies
- Get AI-driven insights (spending pattern analysis, savings optimization suggestions)

### 6. Context-dependent categorization is rigid

Travel spending is handled by duplicating categories with a "путешествия" envelope.
But other contexts (e.g., business trip, hosting guests, holiday season) don't have this mechanism.
Ideally, any receipt could be tagged with a context that overrides its default envelope allocation.

---

## Desired Outcome

### Core requirements

1. **Receipt scanning via QR code** (Serbian fiscal receipts) — automatically fetch and parse individual line items from the tax authority website.

2. **Item-level storage** — every purchased item is stored with its name, quantity, price, and assigned category. Receipt-level totals are derived, not entered.

3. **Automatic categorization with learning** — items are auto-categorized based on rules (pattern matching on item names). Unknown items are flagged for manual classification; once classified, the rule is remembered for future receipts.

4. **Two-level classification (category + envelope)** preserved — the current mental model works. Categories and envelopes should be easy to rename, merge, split, or reorganize at any time, with all historical data instantly reflecting the change (because raw item data is separate from classification rules).

5. **Context tagging** — any receipt (or individual item) can be tagged with a context (e.g., "vacation:bosnia", "business_trip", "guests") that overrides its default envelope for aggregation purposes.

6. **Mobile-first data entry** — scanning QR codes and entering manual expenses must work conveniently from a phone. Manual entry for non-QR expenses (cafés, services, cash payments) should be fast (amount + category, optional comment).

7. **Multi-currency support** — amounts in RSD, EUR, BAM, or other currencies. Conversion rates can be fixed per period or fetched automatically.

8. **Income tracking** — record salary and other income with dates, maintaining the current income-vs-expenses-vs-savings view.

### Analytical requirements

9. **Operational dashboard** — current month snapshot: spent vs. earned, remaining budget by envelope, comparison with previous months. Accessible from phone.

10. **Analytical dashboard** — interactive exploration: arbitrary time ranges, breakdowns by category/envelope/store/context, year-over-year comparisons, trend charts, seasonality detection.

11. **AI-powered analysis** — ability to feed accumulated data to an LLM for insights: spending anomalies, optimization suggestions, forecasting, pattern recognition. This runs locally or through an existing subscription (no separate API costs).

### Data ownership and portability

12. **All data stored locally / under personal control** — no vendor lock-in, no cloud-only storage. The data must be exportable in standard formats (CSV, JSON) at any time.

13. **Google Sheets as optional view layer** — the current spreadsheet can continue to be auto-populated from the primary data store for familiarity, but it is not the source of truth.

### Technical constraints

14. **Free hosting** — the system should run on free-tier infrastructure or locally. The workload is minimal: 10–20 receipts/day, single user.

15. **Professional developer as user** — the system will be built and maintained by the user (experienced programmer). It does not need to be polished consumer software — clean code, clear data model, and scriptability are preferred over UI polish.

16. **Viable as a vibe-coding project** — the scope should be achievable in a few weekends of focused work, building incrementally (receipt parser → storage → categorization → dashboards → AI analysis).
