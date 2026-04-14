# Dinary server

Сервер для [Dinary - ваш динарный дневник](https://github.com/andgineer/dinary).

Учёт расходов, сканирование чеков, анализ трат с помощью AI.

Dinary-server — бэкенд на FastAPI, который:

- Хранит расходы в Google Sheets (с автоматической конвертацией в EUR)
- Парсит QR-коды сербских фискальных чеков (сумма + дата)
- Раздаёт мобильное PWA-приложение для быстрого ввода расходов
- Поддерживает офлайн-очередь записей при отсутствии связи

### Быстрый старт

1. [Настройте Google Sheets](google-sheets-setup.md) — создайте сервисный аккаунт и таблицу.
2. Разверните сервер:
      - [Oracle Cloud Free Tier](deploy-oracle.md) — $0/месяц навсегда
      - [Render](deploy-render.md) — $0-7/месяц
      - [Railway](deploy-railway.md) — $5/месяц
3. Настройте [Cloudflare Tunnel и Access](cloudflare-setup.md) для HTTPS и аутентификации.
4. [Установите PWA](pwa-install.md) на телефон.
