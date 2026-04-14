# Установка

## Docker (рекомендуется)

Простейший способ запустить dinary-server — через Docker:

```bash
git clone https://github.com/andgineer/dinary-server.git
cd dinary-server

# Поместите ключ сервисного аккаунта Google (см. Настройка Google Sheets)
cp /path/to/your-key.json credentials.json

cp .env.example .env
# Отредактируйте .env: укажите GOOGLE_SHEETS_SPREADSHEET_ID
docker compose up -d
```

!!! tip
    Нет ключа сервисного аккаунта? См. [Настройка Google Sheets](google-sheets-setup.md).

Подробные инструкции для конкретных хостингов:

- [Oracle Cloud Free Tier](deploy-oracle.md)
- [Render](deploy-render.md)
- [Railway](deploy-railway.md)

## Локальная разработка

См. [README](https://github.com/andgineer/dinary-server#local-development) для настройки локальной разработки с `uv`.
