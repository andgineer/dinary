# Настройка Google Sheets

Dinary-server хранит runtime-данные в DuckDB, а не в Google Sheets. Google
Sheets используются в двух вспомогательных сценариях: bootstrap import
исторических данных и опциональный append-only sheet logging. Для работы нужен
сервисный аккаунт Google и одна или несколько таблиц, расшаренных на него.

## 1. Создание проекта Google Cloud

1. Перейдите на [console.cloud.google.com](https://console.cloud.google.com/).
2. Нажмите **Выберите проект** → **Новый проект** → назовите его (например, `dinary`) → **Создать**.
3. Выберите созданный проект.

## 2. Включение необходимых API

1. Перейдите в **APIs & Services** → **Library**.
2. Найдите **Google Sheets API** → нажмите → **Enable**.
3. Вернитесь в **Library**, найдите **Google Drive API** → нажмите → **Enable**.

## 3. Создание сервисного аккаунта

1. Перейдите в **APIs & Services** → **Credentials** → **Create Credentials** → **Service account**.
2. Заполните:
      - **Name**: `dinary` (или любое имя)
      - **ID**: генерируется автоматически
3. Нажмите **Create and Continue** → пропустите необязательные шаги → **Done**.

## 4. Скачивание JSON-ключа

1. В разделе **Credentials** нажмите на созданный сервисный аккаунт.
2. Перейдите в **Keys** → **Add Key** → **Create new key** → **JSON** → **Create**.
3. Сохраните скачанный файл как `~/.config/gspread/service_account.json`:

```bash
mkdir -p ~/.config/gspread
mv ~/Downloads/your-project-*.json ~/.config/gspread/service_account.json
```

!!! warning
    Храните этот файл в секрете. Никогда не коммитьте его в Git — он уже добавлен в `.gitignore`.

## 5. Создание и расшаривание таблицы

1. Перейдите на [sheets.google.com](https://sheets.google.com/) → создайте новую таблицу.
2. Назовите её (например, `Dinary Expenses`).
3. Скопируйте ID таблицы из URL: `https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit`.
4. Нажмите **Поделиться** → вставьте email сервисного аккаунта (из JSON-ключа, поле `client_email`, вида `dinary@project-id.iam.gserviceaccount.com`) → роль **Редактор** → **Отправить**.

## 6. Настройка dinary-server

Установите переменные окружения (в `.env` или в настройках хостинга):

| Переменная | Значение |
|------------|----------|
| `DINARY_GOOGLE_SHEETS_CREDENTIALS_PATH` | путь к `service_account.json` (по умолчанию: `~/.config/gspread/service_account.json`) |
| `DINARY_IMPORT_SOURCES_JSON` | JSON-массив с описанием годовых исходных таблиц для bootstrap import |

### Источники для bootstrap import

`DINARY_IMPORT_SOURCES_JSON` используется историческим import-флоу (`inv import-config`, `inv import-catalog`, `inv import-budget`, `inv import-budget-all`, `inv verify-bootstrap-import`, `inv verify-bootstrap-import-all`).

Пример:

```bash
DINARY_IMPORT_SOURCES_JSON=[{"year":2026,"spreadsheet_id":"1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms","worksheet_name":"Sheet1","layout_key":"default"}]
```

Каждый объект описывает один исходный spreadsheet для одного года. Основные поля:

- `year` — бюджетный год
- `spreadsheet_id` — ID Google Sheets spreadsheet
- `worksheet_name` — имя листа для import расходов
- `layout_key` — парсер формата листа (`default`, `rub_6col` и т.д.)
- `income_worksheet_name` — необязательное имя листа для import доходов
- `income_layout_key` — необязательный парсер для import доходов

## 7. Логгинг в таблицу (опционально)

Sheet logging автоматически добавляет каждый новый расход в Google Sheets в реальном времени. Это удобно, если вы хотите строить сводные таблицы или графики в Google Sheets параллельно со встроенной аналитикой Dinary.

### Как это работает

- Каждый `POST /api/expenses` добавляет строку в **первый лист** указанной таблицы.
- 3D-категория/событие/теги проецируются в 2D `(sheet_category, sheet_group)` через таблицу `logging_mapping`. Если маппинг для категории не найден, название категории используется как fallback.
- Один и тот же лист может хранить **несколько лет** одновременно. Строки сортируются по `(год, месяц, sheet_category, sheet_group)`, новые блоки `(год, месяц)` уходят наверх. Год берётся из реального значения колонки A (Google показывает её как, например, `Apr-1`, но хранит `2026-04-01`), поэтому январь 2026 и январь 2027 не путаются между собой.
- В колонке J каждой добавленной строки лежит непрозрачный маркер `[exp:<expense_id>]`. Если предыдущая попытка добавления для того же расхода уже дошла до Google, но ответ был потерян (тайм-аут), следующая попытка увидит маркер и пропустит дублирующую запись.
- Если запись не удалась (ошибка сети, квота), задание остаётся в очереди и повторяется при следующем запуске `inv drain-logging`.

### Включение

Установите `DINARY_SHEET_LOGGING_SPREADSHEET` — ID таблицы или полный URL из браузера:

```bash
# Просто ID таблицы:
DINARY_SHEET_LOGGING_SPREADSHEET=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms

# Или полный URL (ID извлекается автоматически):
DINARY_SHEET_LOGGING_SPREADSHEET=https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit
```

Таблица должна быть расшарена с сервисным аккаунтом из шага 4 (роль «Редактор»).

### Отключение

Оставьте `DINARY_SHEET_LOGGING_SPREADSHEET` пустой или не задавайте. Расходы по-прежнему сохраняются в DuckDB; пропускается только запись в Google Sheets.

### Повторная обработка отложенных задач

Если сервер был перезапущен во время записи в таблицу, выполните:

```bash
inv drain-logging
```

Эта команда проходит все файлы `budget_*.duckdb` и повторяет каждое отложенное задание.
