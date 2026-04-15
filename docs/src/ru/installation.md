# Установка

## Быстрый старт (Docker, локальная разработка)

Простейший способ запустить dinary-server локально — через Docker:

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

## Развёртывание на сервере

- [Oracle Cloud Free Tier](deploy-oracle.md) — $0/месяц, всегда включённая VM
- [Свой компьютер](deploy-selfhost.md) — $0, Tailscale Funnel или Cloudflare Tunnel

## Локальная разработка (без Docker)

См. [README](https://github.com/andgineer/dinary-server#local-development) для настройки локальной разработки с `uv`.
