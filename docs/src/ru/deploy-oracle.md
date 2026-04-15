# Развёртывание на Oracle Cloud Free Tier

Oracle Cloud Always Free предоставляет бессрочные VM — достаточно для работы dinary-server бесплатно.

## Стоимость

| Ресурс | Бесплатный лимит | Стоимость |
|--------|-----------------|-----------|
| AMD Micro VM | 2 инстанса, 1 OCPU + 1 ГБ RAM каждый | $0 навсегда |
| ARM Ampere A1 VM | До 4 OCPU, 24 ГБ RAM (общий пул — часто недоступен) | $0 навсегда |
| Загрузочный диск | 200 ГБ всего | $0 |
| Исходящий трафик | 10 ТБ/месяц | $0 |
| **Итого** | | **$0/месяц** |

!!! tip "Какой shape выбрать"
    **AMD Micro** (`VM.Standard.E2.1.Micro`, 1 ГБ RAM) — рекомендуется. Почти всегда доступен, так как Oracle резервирует отдельный пул для бесплатных аккаунтов. 1 ГБ RAM достаточно для FastAPI без Docker.

    **ARM Ampere A1** (`VM.Standard.A1.Flex`, до 24 ГБ RAM) — мощнее, но часто недоступен ("Out of host capacity"). Если удалось создать — отлично, если нет — берите AMD Micro.

!!! warning
    Oracle может отключить неактивные Always Free инстансы. Работающий сервер dinary поддерживает инстанс активным. Если инстанс отключён — его можно пересоздать, данные хранятся в Google Sheets, а не на VM.

## Требования

- JSON-ключ сервисного аккаунта Google и ID таблицы — см. [Настройка Google Sheets](google-sheets-setup.md).
- Пара SSH-ключей для подключения к VM.

## 1. Создание аккаунта

1. Перейдите на [cloud.oracle.com](https://cloud.oracle.com/) → **Sign Up**.
2. Выберите регион (нельзя изменить позже).
3. Завершите верификацию (нужна банковская карта, но списания не будет для Always Free ресурсов).

!!! tip "Выбор региона"
    Регион выбирается навсегда. Доступность ARM-инстансов зависит от региона. По отзывам, **Ashburn**, **Phoenix**, **Frankfurt** и **London** чаще имеют свободные ARM-мощности. AMD Micro доступен во всех регионах.

!!! note
    Подтверждение аккаунта может занять от нескольких часов до нескольких дней.

## 2. Настройка сети (VCN)

Для VM нужна Virtual Cloud Network (VCN) с public subnet и internet gateway. Создайте их **до** создания VM:

1. Перейдите в **Networking** → **Virtual Cloud Networks** → **Create VCN**.
      - **Name**: `dinary-vcn` (или любое имя)
      - **IPv4 CIDR Blocks**: `10.0.0.0/16`
      - Нажмите **Create VCN**.

2. Внутри созданной VCN → **Subnets** → **Create Subnet**.
      - **Name**: `public-subnet`
      - **Subnet type**: Regional
      - **IPv4 CIDR Block**: `10.0.0.0/24`
      - **Subnet access**: **Public Subnet**
      - Нажмите **Create Subnet**.

3. Внутри VCN → **Internet Gateways** → **Create Internet Gateway**.
      - **Name**: `internet-gw`
      - Нажмите **Create Internet Gateway**.

4. Внутри VCN → **Route Tables** → нажмите на дефолтную route table → **Add Route Rules**.
      - **Destination CIDR Block**: `0.0.0.0/0`
      - **Target Type**: Internet Gateway
      - **Target**: выберите `internet-gw`
      - Нажмите **Add Route Rules**.

## 3. Создание VM

1. Перейдите в **Compute** → **Instances** → **Create Instance**.
2. Настройте:
      - **Image**: `Canonical Ubuntu 22.04 Minimal` (для AMD Micro — без `aarch64` в названии)
      - **Shape**: `VM.Standard.E2.1.Micro` — 1 OCPU, 1 ГБ RAM
      - **Capacity**: `On-demand capacity`
      - **Availability / Live migration**: `Let Oracle Cloud Infrastructure choose the best migration option`
      - **Networking**: выберите `dinary-vcn` → выберите `public-subnet` → поставьте галку **Automatically assign public IPv4 address**
      - **SSH keys**: загрузите ваш публичный ключ
      - **Cloud-init script**: оставьте пустым
3. Нажмите **Create**.

!!! tip "Альтернатива: ARM"
    Если ARM-мощности доступны, можно выбрать `Canonical Ubuntu 22.04 Minimal aarch64` + shape `VM.Standard.A1.Flex` (1 OCPU, 6 ГБ RAM). Больше RAM позволяет использовать Docker при желании. Остальная настройка такая же.

## 4. Подключение к VM

```bash
ssh ubuntu@<PUBLIC_IP>
```

## 5. Настройка файрвола

!!! tip
    **Используете Cloudflare Tunnel или Tailscale Funnel?** Пропустите этот шаг — туннель подключается исходящим соединением, открывать входящие порты не нужно.

Если нужен прямой доступ к порту 8000, откройте его в обоих файрволах Oracle:

### Security List VCN

1. Перейдите в **Networking** → **Virtual Cloud Networks** → ваша VCN → **Security Lists**.
2. Добавьте правило входящего трафика: Source `0.0.0.0/0`, Protocol TCP, Destination Port `8000`.

### Файрвол ОС

```bash
sudo iptables -I INPUT -p tcp --dport 8000 -j ACCEPT
sudo netfilter-persistent save
```

## 6. Загрузка ключа сервисного аккаунта Google

Выполните **на вашем ноутбуке** (не на VM) для копирования файла ключа на сервер:

```bash
scp ~/.config/gspread/service_account.json ubuntu@<PUBLIC_IP>:~/credentials.json
```

См. [Настройка Google Sheets](google-sheets-setup.md) если у вас его ещё нет.

## 7. Установка Python и dinary-server

Выполните эти команды **на VM** (SSH-сессия):

```bash
sudo apt update && sudo apt install -y python3 python3-pip git

# Установите uv (быстрый менеджер пакетов Python)
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env

# Клонируйте и установите
git clone https://github.com/andgineer/dinary-server.git
cd dinary-server
uv sync --no-dev

# Переместите файл credentials в директорию проекта
mv ~/credentials.json .
```

## 8. Запуск как systemd-сервис

Создайте файл сервиса, чтобы dinary-server запускался автоматически и перезапускался при сбоях:

```bash
sudo tee /etc/systemd/system/dinary.service << 'EOF'
[Unit]
Description=dinary-server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/dinary-server
Environment=DINARY_GOOGLE_SHEETS_SPREADSHEET_ID=your-spreadsheet-id
ExecStart=/home/ubuntu/.local/bin/uv run uvicorn dinary.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

Замените `your-spreadsheet-id` на фактический ID вашей Google Sheets таблицы (длинная строка из URL таблицы).

Включите и запустите:

```bash
sudo systemctl daemon-reload
sudo systemctl enable dinary
sudo systemctl start dinary
```

Проверка (подождите 10-20 секунд при первом запуске — uv скачивает зависимости):

```bash
curl http://localhost:8000/api/health
```

## 9. Настройка Tailscale Funnel (HTTPS-доступ)

Tailscale Funnel открывает dinary-server в интернет через HTTPS без открытия портов файрвола и без покупки домена.

### Установка Tailscale на VM

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Команда выведет URL — откройте его в браузере для входа в Tailscale (создайте бесплатный аккаунт если нет).

### Включение HTTPS и Funnel

В [админ-консоли Tailscale](https://login.tailscale.com/admin/dns):

1. Включите **MagicDNS** (если ещё не включён).
2. Включите **HTTPS** для вашего tailnet.

### Запуск Funnel

```bash
sudo tailscale funnel 8000
```

Tailscale покажет публичный URL, например `https://instance-20260414.tail1234.ts.net`. Этот URL доступен откуда угодно (телефон, другие устройства) по HTTPS.

!!! warning "Первый запуск: подождите до 10 минут"
    При первом запуске Tailscale создаёт TLS-сертификат и распространяет DNS. URL может возвращать `ERR_SSL_PROTOCOL_ERROR` несколько минут. Подождите и повторите — заработает.

### Запуск Funnel как сервис

Чтобы Funnel продолжал работать после закрытия SSH-сессии:

```bash
sudo tee /etc/systemd/system/tailscale-funnel.service << 'EOF'
[Unit]
Description=Tailscale Funnel for dinary
After=tailscaled.service

[Service]
Type=simple
ExecStart=/usr/bin/tailscale funnel 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable tailscale-funnel
sudo systemctl start tailscale-funnel
```

Проверка: откройте `https://<your-machine>.ts.net/api/health` в браузере телефона.

!!! tip "Альтернатива: Cloudflare Tunnel"
    Если ваш домен управляется через Cloudflare DNS, можно использовать [Cloudflare Tunnel и Access](cloudflare-setup.md) — кастомный домен и авторизация по email. Требует переноса DNS домена в Cloudflare (полная настройка, бесплатно).

## Обслуживание

- **Логи**: `sudo journalctl -u dinary -f`
- **Обновление**: `cd ~/dinary-server && git pull && uv sync --no-dev && sudo systemctl restart dinary`
- **Перезапуск**: `sudo systemctl restart dinary`
- **Статус**: `sudo systemctl status dinary`
