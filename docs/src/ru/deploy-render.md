# Развёртывание на Render

Render предоставляет хостинг контейнеров с автодеплоем из GitHub. Бесплатный план работает для dinary-server, но имеет холодный старт.

## Стоимость

| План | Стоимость | RAM | Всегда активен | Холодный старт |
|------|-----------|-----|----------------|---------------|
| Free | $0/месяц | 512 МБ | Нет — засыпает через 15 мин без активности | ~30 секунд |
| Starter | $7/месяц | 512 МБ | Да | Нет |

!!! note
    Бесплатный план засыпает через 15 минут без активности. Первый запрос после засыпания занимает ~30 секунд. Для личного трекера расходов, используемого несколько раз в день, это заметно, но терпимо. План Starter за $7/месяц устраняет холодный старт.

## 1. Требования

- Аккаунт GitHub с загруженным репозиторием `dinary-server`.
- JSON-ключ сервисного аккаунта Google и ID таблицы — см. [Настройка Google Sheets](google-sheets-setup.md).

## 2. Создание Web Service

1. Перейдите на [render.com](https://render.com/) → **Sign up** (вход через GitHub).
2. Нажмите **New** → **Web Service**.
3. Подключите репозиторий `dinary-server`.
4. Настройте:
      - **Name**: `dinary-server`
      - **Region**: ближайший к вам
      - **Runtime**: Docker
      - **Instance Type**: Free (или Starter за $7/месяц)
5. Нажмите **Create Web Service**.

## 3. Переменные окружения

В панели Render → ваш сервис → **Environment**:

| Переменная | Значение |
|------------|----------|
| `DINARY_GOOGLE_SHEETS_SPREADSHEET_ID` | ID вашей таблицы |
| `DINARY_GOOGLE_SHEETS_CREDENTIALS_PATH` | `/etc/secrets/credentials.json` |
| `DINARY_LOG_JSON` | `true` |

## 4. Добавление ключа сервисного аккаунта

Render поддерживает **Secret Files**:

1. Перейдите в сервис → **Environment** → **Secret Files**.
2. Добавьте файл:
      - **Filename**: `/etc/secrets/credentials.json`
      - **Contents**: вставьте полное содержимое JSON-ключа
3. Сохраните.

## 5. Деплой

Render автоматически деплоит при каждом push в основную ветку. Также можно запустить деплой вручную из панели.

Проверка: откройте `https://dinary-server.onrender.com/api/health`.

## 6. Свой домен + Cloudflare Access

1. В Render → сервис → **Settings** → **Custom Domains** → добавьте `dinary.yourdomain.com`.
2. В Cloudflare DNS добавьте CNAME: `dinary` → `dinary-server.onrender.com` (proxied).
3. Следуйте [инструкции по Cloudflare Access](cloudflare-setup.md#6-настройка-cloudflare-access) для аутентификации.

!!! note
    С Render не нужен Cloudflare Tunnel — Render уже предоставляет HTTPS. Cloudflare нужен только для DNS + аутентификации Access.

## Обслуживание

- **Логи**: панель Render → сервис → **Logs**.
- **Обновление**: push в GitHub — автодеплой.
- **Перезапуск**: панель → **Manual Deploy** → **Clear build cache & deploy**.
