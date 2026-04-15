# Деплой на свой компьютер

Запустите dinary-server на своём Mac или PC и откройте доступ через интернет с помощью туннеля. Бесплатно, всегда работает пока компьютер включён, и совпадает с долгосрочной архитектурой (десктопный AI-агент будет работать на том же компьютере).

## Стоимость

| Ресурс | Стоимость |
|--------|-----------|
| Ваш компьютер | Уже есть |
| Tailscale Funnel | $0 (бесплатный Personal план) |
| Cloudflare Tunnel | $0 (бесплатный план) |
| **Итого** | **$0/месяц** |

## Требования

- JSON-ключ сервисного аккаунта Google и ID таблицы — см. [Настройка Google Sheets](google-sheets-setup.md).
- dinary-server запущен локально (см. [README](https://github.com/andgineer/dinary-server#local-development)).

## Вариант A: Tailscale Funnel

Tailscale Funnel открывает локальный порт в публичный интернет через HTTPS. Простая настройка, но URL будет `*.ts.net` (без кастомного домена).

### 1. Установите Tailscale

- **macOS**: `brew install tailscale` или скачайте с [tailscale.com/download](https://tailscale.com/download)
- **Windows**: скачайте с [tailscale.com/download](https://tailscale.com/download)
- **Linux**: `curl -fsSL https://tailscale.com/install.sh | sh`

Войдите в аккаунт и подключитесь к tailnet.

### 2. Включите Funnel

В [админ-консоли Tailscale](https://login.tailscale.com/admin/dns):

1. Включите **MagicDNS** (если ещё не включён).
2. Включите **HTTPS** для вашего tailnet.

### 3. Запустите dinary-server

```bash
cd dinary-server
export DINARY_GOOGLE_SHEETS_SPREADSHEET_ID="your-spreadsheet-id"
uv run uvicorn dinary.main:app --host 127.0.0.1 --port 8000
```

### 4. Откройте через Funnel

В отдельном терминале:

```bash
tailscale funnel 8000
```

Tailscale покажет публичный URL, например `https://your-machine.your-tailnet.ts.net`. Этот URL доступен откуда угодно (телефон, другие устройства) по HTTPS.

!!! note
    Funnel в бета-версии. Распространение DNS может занять несколько минут при первой настройке.

### 5. Работа в фоне

Чтобы dinary-server продолжал работать после закрытия терминала:

=== "macOS (launchd)"

    ```bash
    # Используйте менеджер процессов или nohup
    nohup uv run uvicorn dinary.main:app --host 127.0.0.1 --port 8000 &
    ```

=== "Linux (systemd)"

    См. шаг 7 в [инструкции по деплою на Oracle](deploy-oracle.md) для примера systemd-сервиса.

=== "Windows"

    Используйте Планировщик задач или запустите как Windows Service через [NSSM](https://nssm.cc/).

## Вариант B: Cloudflare Tunnel

Cloudflare Tunnel поддерживает кастомные домены и Cloudflare Access для авторизации. См. отдельную инструкцию [Настройка Cloudflare Tunnel и Access](cloudflare-setup.md).

## Когда компьютер выключен

PWA сохраняет записи в IndexedDB когда сервер недоступен. Когда вы включите компьютер и туннель переподключится, PWA автоматически синхронизирует все накопленные записи при следующем открытии.

## Сравнение

| | Tailscale Funnel | Cloudflare Tunnel |
|---|---|---|
| **Настройка** | Проще | Больше шагов |
| **URL** | `*.ts.net` (задан Tailscale) | Ваш собственный домен |
| **Авторизация** | Нет встроенной | Cloudflare Access (email OTP) |
| **Кастомный домен** | Не поддерживается | Поддерживается |
| **Статус** | Бета | Стабильный |
