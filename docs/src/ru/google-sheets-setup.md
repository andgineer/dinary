# Настройка Google Sheets

Dinary-server хранит расходы в таблице Google Sheets. Для работы нужен сервисный аккаунт Google
и таблица, к которой он имеет доступ.

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
| `DINARY_GOOGLE_SHEETS_SPREADSHEET_ID` | ID таблицы из шага 5 |
| `DINARY_GOOGLE_SHEETS_CREDENTIALS_PATH` | путь к `credentials.json` (по умолчанию: `credentials.json` в рабочей директории) |

!!! note
    При использовании `docker compose` с `.env` указывайте `GOOGLE_SHEETS_SPREADSHEET_ID` (без префикса `DINARY_`) — `docker-compose.yml` добавляет префикс автоматически. При прямом запуске (Oracle VM, локальная разработка) указывайте `DINARY_GOOGLE_SHEETS_SPREADSHEET_ID`.
