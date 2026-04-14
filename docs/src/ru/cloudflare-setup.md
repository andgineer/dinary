# Настройка Cloudflare Tunnel и Access

Cloudflare обеспечивает HTTPS и аутентификацию для dinary-server бесплатно (до 50 пользователей).

## Требования

- Домен, управляемый через Cloudflare (бесплатный план подходит).
- dinary-server запущен на `http://localhost:8000`.

!!! note
    `cloudflared` устанавливается на шаге 1 ниже. Пользователям Render и Railway нужен только [шаг 6 (Cloudflare Access)](#6-настройка-cloudflare-access) — эти платформы предоставляют HTTPS, поэтому туннель не нужен.

## 1. Установка cloudflared

=== "Ubuntu/Debian"
    ```bash
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
    sudo apt update && sudo apt install cloudflared
    ```

=== "macOS"
    ```bash
    brew install cloudflared
    ```

=== "Docker"
    ```bash
    docker pull cloudflare/cloudflared:latest
    ```

## 2. Создание туннеля

```bash
cloudflared tunnel login
cloudflared tunnel create dinary
```

Будет создан туннель, а credentials сохранятся в `~/.cloudflared/<TUNNEL_ID>.json`.

## 3. Конфигурация туннеля

Создайте файл `~/.cloudflared/config.yml`:

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /home/<YOUR_USER>/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: dinary.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
```

Замените `<YOUR_USER>` на ваше имя пользователя Linux (или используйте `/root/`, если работаете от root).

## 4. Настройка DNS

```bash
cloudflared tunnel route dns dinary dinary.yourdomain.com
```

Эта команда создаёт CNAME-запись, указывающую `dinary.yourdomain.com` на туннель.

## 5. Запуск туннеля

```bash
cloudflared tunnel run dinary
```

Для запуска как системный сервис:

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
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
