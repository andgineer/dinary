# Dinary

Учёт расходов, сканирование чеков (Сербия, Черногория), анализ трат с помощью AI.

Dinary server — бэкенд на FastAPI, который:

- Хранит расходы в локальной SQLite-базе (в EUR, с исходной суммой и валютой для аудита)
- Опционально дублирует каждый расход в Google Sheets (конвертируя в RSD для сводной таблицы)
- Парсит QR-коды сербских фискальных чеков (сумма + дата)
- Раздаёт мобильное PWA-приложение для быстрого ввода расходов в динарах
- Поддерживает офлайн-очередь записей при отсутствии связи

<table>
<tr>
<td align="center" valign="top"><sub><b>Выберите набор категорий при первом запуске</b></sub><br/><img src="images/screenshots/IMG_2583.PNG" width="280"/></td>
<td align="center" valign="top"><sub><b>Чеки классифицируются AI</b></sub><br/><img src="images/screenshots/IMG_2588.PNG" width="280"/><br/><img src="images/screenshots/IMG_2584.PNG" width="280"/></td>
<td align="center" valign="top"><sub><b>Ввод в любой валюте — одно касание</b></sub><br/><img src="images/screenshots/IMG_2585.PNG" width="280"/><br/><img src="images/screenshots/IMG_2586.PNG" width="280"/></td>
</tr>
</table>

## Быстрый старт {#quick-start}

Dinary — ваш собственный небольшой сервер, а приложение на телефоне — страница,
которую он отдаёт. Он бесплатно работает на виртуалке Oracle Cloud Always Free и
доступен через вашу приватную сеть Tailscale.

=== "С ИИ-агентом"

    Есть подписка Claude Pro или Max либо ChatGPT Plus? Откройте вкладку **Code**
    в настольном приложении Claude или **Codex** в настольном приложении ChatGPT
    и вставьте:

    ```text
    Установи мне dinary на Oracle Cloud по инструкции
    https://andgineer.github.io/dinary/agent-install/
    ```

    Агент по шагам скажет, что можете сделать только вы, — зарегистрироваться в
    Oracle и Tailscale, вписать ключ в файл, — а остальное сделает сам.

=== "Вручную"

    1. Разверните сервер:
          - [Oracle Cloud Free Tier](deploy-oracle.md) — $0/месяц навсегда
          - [Свой компьютер](deploy-selfhost.md) — $0 (Tailscale Funnel или Cloudflare Tunnel)
    2. Настройте HTTPS-доступ — см. инструкции по деплою выше.
    3. [Установите PWA](pwa-install.md) на телефон.
    4. Первоначально загружается [классификатор](taxonomy.md), который вы далее можете
       корректировать.
    5. При желании [настройте Google Sheets](google-sheets-setup.md), чтобы каждый
       расход попадал строкой в таблицу.
    6. Запустите `inv analytics` — своего [персонального финансового аналитика](analytics.md).
