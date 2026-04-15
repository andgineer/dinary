# Настройка Cloudflare Tunnel и Access

Cloudflare Tunnel создаёт зашифрованное соединение от машины с dinary-server до сети Cloudflare. Это даёт HTTPS и кастомный домен без открытия портов в файрволе. Cloudflare Access добавляет авторизацию по email (бесплатно до 50 пользователей).

Все команды в шагах 1-5 выполняются **на машине, где работает dinary-server** (Oracle Cloud VM, ваш Mac/PC и т.д.).

## Требования

- Домен, управляемый через Cloudflare (бесплатный план подходит). Если домена нет, можно купить прямо в Cloudflare (~$10/год за `.com`).
- dinary-server запущен на `http://localhost:8000` на целевой машине.

## 1. Установка cloudflared на сервере

Подключитесь по SSH к серверу (или откройте терминал на Mac/PC) и установите `cloudflared`:

=== "Ubuntu/Debian (Oracle Cloud VM)"
    ```bash
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
    sudo apt update && sudo apt install cloudflared
    ```

=== "macOS (свой компьютер)"
    ```bash
    brew install cloudflared
    ```

## 2. Создание туннеля

```bash
cloudflared tunnel login
```

Откроется браузер для авторизации в Cloudflare. Если работаете на headless-сервере (Oracle VM), скопируйте URL из консоли и откройте его на ноутбуке.

```bash
cloudflared tunnel create dinary
```

Credentials сохранятся в `~/.cloudflared/<TUNNEL_ID>.json`.

## 3. Конфигурация туннеля

Создайте файл `~/.cloudflared/config.yml`:

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /home/ubuntu/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: dinary.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
```

Замените `/home/ubuntu/` на вашу домашнюю директорию (например, `/Users/yourname/` на macOS).

## 4. Настройка DNS

```bash
cloudflared tunnel route dns dinary dinary.yourdomain.com
```

Эта команда создаёт CNAME-запись, указывающую `dinary.yourdomain.com` на туннель.

## 5. Запуск туннеля

```bash
cloudflared tunnel run dinary
```

Для запуска как системный сервис (чтобы работал после перезагрузки):

=== "Linux (Oracle VM)"
    ```bash
    sudo cloudflared service install
    sudo systemctl enable cloudflared
    sudo systemctl start cloudflared
    ```

=== "macOS"
    ```bash
    sudo cloudflared service install
    sudo launchctl start com.cloudflare.cloudflared
    ```

Проверка: откройте `https://dinary.yourdomain.com/api/health` в браузере.

## 6. Настройка Cloudflare Access

1. Перейдите в [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) → **Access** → **Applications**.
2. Нажмите **Add an application** → **Self-hosted**.
3. Заполните:
      - **Application name**: Dinary
      - **Application domain**: `dinary.yourdomain.com`
      - **Session duration**: 30 days (или по вашему выбору)
4. Нажмите **Next** → **Add a policy**:
      - **Policy name**: Allowed users
      - **Action**: Allow
      - **Include** → **Emails**: добавьте email-адреса разрешённых пользователей
5. Нажмите **Save**.

Теперь все запросы к `dinary.yourdomain.com` требуют аутентификации через email OTP или Google OAuth.

## Стоимость

| Компонент | Стоимость |
|-----------|-----------|
| Cloudflare DNS (бесплатный план) | $0 |
| Cloudflare Tunnel | $0 |
| Cloudflare Access (до 50 пользователей) | $0 |
| **Итого** | **$0** |
