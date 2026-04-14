# Развёртывание на Oracle Cloud Free Tier

Oracle Cloud Always Free предоставляет бессрочную ARM-виртуалку — достаточно для работы dinary-server бесплатно.

## Стоимость

| Ресурс | Бесплатный лимит | Стоимость |
|--------|-----------------|-----------|
| ARM Ampere A1 VM | До 4 OCPU, 24 ГБ RAM | $0 навсегда |
| Загрузочный диск | 200 ГБ всего | $0 |
| Исходящий трафик | 10 ТБ/месяц | $0 |
| **Итого** | | **$0/месяц** |

!!! warning
    Oracle может отключить неактивные Always Free инстансы. Работающий сервер dinary поддерживает инстанс активным. Если инстанс отключён — его можно пересоздать, данные хранятся в Google Sheets, а не на VM.

## Требования

- JSON-ключ сервисного аккаунта Google и ID таблицы — см. [Настройка Google Sheets](google-sheets-setup.md).
- Пара SSH-ключей для подключения к VM.

## 1. Создание аккаунта

1. Перейдите на [cloud.oracle.com](https://cloud.oracle.com/) → **Sign Up**.
2. Выберите регион (нельзя изменить позже — выберите ближайший).
3. Завершите верификацию (нужна банковская карта, но списания не будет для Always Free ресурсов).

!!! note
    Подтверждение аккаунта может занять от нескольких часов до нескольких дней.

## 2. Создание VM

1. Перейдите в **Compute** → **Instances** → **Create Instance**.
2. Настройте:
      - **Image**: Ubuntu 22.04 (или 24.04) Minimal — ARM
      - **Shape**: VM.Standard.A1.Flex — 1 OCPU, 6 ГБ RAM (достаточно для dinary)
      - **Networking**: создайте VCN с публичной подсетью, назначьте публичный IP
      - **SSH keys**: загрузите ваш публичный ключ
3. Нажмите **Create**.

## 3. Настройка файрвола

!!! tip
    **Используете Cloudflare Tunnel (рекомендуется)?** Пропустите весь этот шаг — туннель подключается исходящим соединением, открывать входящие порты не нужно.

Если нужен прямой доступ к порту 8000 (без Cloudflare Tunnel), откройте его в обоих файрволах Oracle:

### Security List VCN

1. Перейдите в **Networking** → **Virtual Cloud Networks** → ваша VCN → **Security Lists**.
2. Добавьте правило входящего трафика: Source `0.0.0.0/0`, Protocol TCP, Destination Port `8000`.

### Файрвол ОС

```bash
sudo iptables -I INPUT -p tcp --dport 8000 -j ACCEPT
sudo netfilter-persistent save
```

## 4. Установка Docker

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER
```

Выйдите и войдите снова, чтобы группа применилась.

## 5. Развёртывание dinary-server

```bash
git clone https://github.com/andgineer/dinary-server.git
cd dinary-server

# Скопируйте ключ сервисного аккаунта Google (см. Настройка Google Sheets)
cp /path/to/your-key.json credentials.json

# Настройте конфигурацию
cp .env.example .env
# Отредактируйте .env: укажите GOOGLE_SHEETS_SPREADSHEET_ID

# Соберите и запустите
docker compose up -d
```

Проверка:

```bash
curl http://localhost:8000/api/health
```

## 6. Настройка Cloudflare Tunnel

Следуйте инструкции [Настройка Cloudflare Tunnel и Access](cloudflare-setup.md) для настройки HTTPS и аутентификации.

## Обслуживание

- **Логи**: `docker compose logs -f`
- **Обновление**: `git pull && docker compose up -d --build`
- **Перезапуск**: `docker compose restart`
