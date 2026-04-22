# Установка

## Быстрый старт (Docker, локальная разработка)

Простейший способ запустить dinary локально — через Docker:

```bash
git clone https://github.com/andgineer/dinary.git
cd dinary

# Поместите ключ сервисного аккаунта Google туда, где его ждёт docker-compose.yml
mkdir -p ~/.config/gspread
cp /path/to/your-key.json ~/.config/gspread/service_account.json

mkdir -p .deploy
cp .deploy.example/.env .deploy/.env
# Отредактируйте .deploy/.env при необходимости (логгинг в таблицу, путь к credentials и т.д.)
docker compose up -d
```

!!! tip
    Нет ключа сервисного аккаунта? См. [Настройка Google Sheets](google-sheets-setup.md).

## Развёртывание на сервере

- [Oracle Cloud Free Tier](deploy-oracle.md) — $0/месяц, всегда включённая VM
- [Свой компьютер](deploy-selfhost.md) — $0, Tailscale Funnel или Cloudflare Tunnel

## Локальная разработка (без Docker)

См. [README](https://github.com/andgineer/dinary#local-development) для настройки локальной разработки с `uv`.
