# План: обновление PWA-статистики и детализация по событиям

## Задача

1. Стат-страница PWA должна обновляться при добавлении расходов/доходов и при
   сканировании чека. Логика кэширования — как у review-страницы: данные считаются
   «грязными» (dirty), пока все чеки не будут полностью обработаны сервером.
2. По клику на событие показывать статистику события: сколько потрачено по каждой
   категории, а также сумму трат за каждый из последних нескольких дней.

## Контекст (текущее состояние)

Стат-страница = `webapp/src/views/AnalyticsView.vue` + стор
`webapp/src/stores/analytics.js` + эндпойнт `GET /api/analytics/summary`
(`src/dinary/api/analytics.py`).

Сейчас кэш аналитики устроен иначе, чем у review:
- `analytics.js` — только TTL на 24 ч (`lastFetched`), без dirty-флага.
- Инвалидацию вызывает только income-стор (`invalidate()` при add/patch/remove).
  Добавление расхода и сканирование чека аналитику не сбрасывают.
- Review-стор использует общий composable `useStaleCache` с dirty-флагом и
  правилом «остаёмся dirty, пока серверная очередь чеков непуста»
  (`review.loadNextPage` перепомечает себя и `llm`-стор, пока `receipts_queue`
  не обнулится). Именно это поведение нужно перенести в аналитику.

События уже приходят в `summary.events` как
`{ id, name, date_range, total, currency, open }` и рисуются строками в
`AnalyticsView`. Клика/раскрытия у них нет.

Схема БД: `events(id,name,date_from,date_to,…)`,
`expenses(amount, datetime, category_id, event_id,…)`,
`categories(id,name,group_id)`, `category_groups(id,name)`.

## Часть 1. Инвалидация кэша как у review (dirty-until-processed)

### 1.1 Перевести `stores/analytics.js` на `useStaleCache`
- Подключить `useStaleCache({ dirtyKey: "dinary:analytics:dirty",
  fetchedKey: "dinary:analytics:fetchedAt", dataKey: "dinary:analytics:v1" })`.
- Хранить `summary/events/trends` через `readCache`/`writeCache` (единый объект).
- Заменить TTL-проверку в `fetchAll` на `isStale()`; после успешного фетча —
  `stampFresh()` + `writeCache(...)`.
- Переименовать метод в `loadIfNeeded()` (единообразие с review/income);
  экспортировать `markDirty`.
- Убрать `invalidate()`; вместо него — `markDirty()`.

### 1.2 Проставлять dirty во всех точках, меняющих расходы/доходы/чеки

| Триггер | Файл | Действие |
|---|---|---|
| Новый расход ушёл на сервер | `composables/flushQueue.js` | при `anyFlushed` → `useAnalyticsStore().markDirty()` |
| Чек отсканирован (не дубликат) | `composables/flushReceiptQueue.js` | рядом с `useLlmStore().markDirty()` / `useReviewStore().markDirty()` → `useAnalyticsStore().markDirty()` |
| Серверная очередь чеков непуста | `stores/review.js` `loadNextPage` | в блоке `if (q.pending>0 …)` добавить `useAnalyticsStore().markDirty()` — это и есть «dirty, пока все чеки не обработаны» |
| Правка категории / расхода / удаление / подтверждение правил / разбор застрявшего чека / удаление чека | `stores/review.js` (`correct`, `updateExpense`, `deleteExpense`, `confirmAll`, `resolveStuckReceipt`, `deleteReceipt`) | `useAnalyticsStore().markDirty()` |
| Доход add/patch/remove | `stores/income.js` | заменить 3× `useAnalyticsStore().invalidate()` → `markDirty()` |

### 1.3 Показ свежих данных
- `AnalyticsView.onMounted` → `store.loadIfNeeded()` (фетчит только если
  `isStale()`), под условием `isOnline`.
- Фоновые пробы в `App.vue` (visibility/online/init) для аналитики не добавляем —
  у неё нет бейджа, она грузится при открытии вкладки; вкладочный
  `loadIfNeeded()` при stale полностью закрывает требование (осознанное сужение
  scope).

Итог: после добавления расхода/дохода или скана чека стор помечается dirty; пока
сервер дообрабатывает чеки — перепомечается через `review.loadNextPage`; первое
открытие стат-страницы после обработки делает свежий фетч и снимает флаг — ровно
семантика review.

## Часть 2. Детализация по событию (по клику)

### 2.1 Бэкенд: `GET /api/analytics/events/{event_id}`
Новый роут в `src/dinary/api/analytics.py`, 404 при отсутствии события. Ответ:

```
{ id, name, date_range, total, currency, open,
  categories: [{ category_id, category_name, group_name, total, currency }],  # sort desc
  days:       [{ date, date_label, total, currency }] }                       # последние N дней с тратами
```

Две новые SQL в `src/dinary/db/sql/`:
- `analytics_event_categories.sql` — `expenses JOIN categories JOIN
  category_groups WHERE event_id=? GROUP BY category ORDER BY SUM(amount) DESC`.
- `analytics_event_days.sql` — `WHERE event_id=? GROUP BY date(datetime)
  ORDER BY day DESC LIMIT 7` (последние несколько дней события, свежие сверху).

Переиспользуем `_fmt`, `settings.accounting_currency`; для `date_label` — короткий
формат дня (напр. «14 Jul»).

### 2.2 Фронтенд
- `api/analytics.js` → `fetchEventDetail(eventId)`.
- `stores/analytics.js` → `eventDetails` (map по id, в памяти), `loadEventDetail(id)`
  (фетч + кэш), сброс `eventDetails` при `reset()`/новом фетче (чтобы деталь тоже
  подчинялась dirty-флагу).
- `AnalyticsView.vue` — сделать строку события раскрывающейся (accordion).
  Важно: для раскрытия завести отдельное состояние `expandedEventId` — поле
  `ev.open` уже занято (значит «событие ещё идёт»), путать нельзя. При раскрытии:
  фетч деталей (skeleton на время загрузки), затем два блока в стиле текущих
  карточек:
  - По категориям — строки «категория · сумма» с пропорциональной полоской
    (inline-CSS bar, без сторонних либ — их в проекте нет), сортировка по убыванию.
  - По дням — строки «день · сумма» за последние несколько дней.

## Часть 3. Тесты (обязательно, в этой же сессии)

Python (`tests/api/test_api_analytics.py`, класс `TestAnalyticsEventDetail`):
- разбивка по категориям отсортирована по убыванию и суммы верны;
- дневная разбивка возвращает корректные суммы и лимит;
- 404 на несуществующее событие;
- валюта = accounting_currency, форматирование с пробелами.

Frontend (`webapp/tests/`):
- новый `store-analytics.test.js`: `markDirty` → `isStale` → рефетч;
  `loadIfNeeded` пропускает фетч на свежем кэше; кэш деталей события.
- расширить `composable-flush-queue.test.js` и
  `composable-flush-receipt-queue.test.js`: проверка `analytics.markDirty`.
- расширить `store-review.test.js`: перепометка аналитики при непустой очереди чеков.
- обновить `store-income.test.js` под `markDirty` вместо `invalidate`.
- деталь события в компоненте — покрыть в новом/существующем тесте вьюхи.

## Часть 4. Спеки
- `specs/reference/pwa-analytics.md`: раздел «Client cache» переписать под
  dirty-флаг (расход/доход/чек, dirty-until-processed); добавить эндпойнт деталей
  события и описание разбивок «по категориям» и «по последним дням».
- `specs/reference/frontend-cache.md`: добавить раздел «Analytics store dirty-flag
  sources» по образцу review/llm.

(Спеки — только текущее состояние и правила, без сигнатур/полей — по требованиям
CLAUDE.md.)

## Порядок работ и гейт готовности
1. Рефактор `analytics.js` на `useStaleCache` + правки income-стора.
2. Проставить `markDirty` во всех точках (flushQueue, flushReceiptQueue,
   review-мутации + очередь).
3. Бэкенд-эндпойнт деталей + 2 SQL + тесты.
4. UI раскрытия события + стор-детали + тесты.
5. Обновить спеки.
6. `scripts/setup-test-env.sh` (при необходимости), затем гейт:
   `uv run inv pre` → «All checks passed!» и `uv run pytest` → `N passed`,
   плюс `cd webapp && npm test` зелёный. `inv pre` — после каждого батча.

Затрагиваемые файлы: `stores/analytics.js`, `stores/income.js`, `stores/review.js`,
`composables/flushQueue.js`, `composables/flushReceiptQueue.js`,
`views/AnalyticsView.vue`, `api/analytics.js`, `src/dinary/api/analytics.py`,
2 новых `.sql`, тесты (py + webapp), 2 спека.

## Открытые вопросы (дефолты)
- «последние несколько дней» = последние 7 дней события с тратами (не глобальные).
- UI детали = инлайн-раскрытие (accordion) в строке. Альтернатива — bottom-sheet
  (`BaseSheet`), как в остальном приложении.
