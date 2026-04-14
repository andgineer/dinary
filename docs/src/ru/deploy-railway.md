# Развёртывание на Railway

Railway запускает Docker-контейнеры с оплатой по потреблению и автодеплоем из GitHub.

## Стоимость

| Компонент | Стоимость |
|-----------|-----------|
| Пробный кредит | $5 бесплатно (одноразово, без карты) |
| Hobby план | $5/месяц (включает $5 кредита на использование) |
| Вычисления (сверх кредита) | ~$0.000463/мин за vCPU |
| Память (сверх кредита) | ~$0.000231/мин за ГБ |

Для dinary-server (~20 запросов/день, минимум CPU) расход укладывается в кредит $5/месяц — **фактически $5/месяц без доплат**.

!!! tip
    В отличие от бесплатного плана Render, Railway не усыпляет сервисы — нет холодного старта.

## 1. Требования

- Аккаунт GitHub с загруженным репозиторием `dinary-server`.
- JSON-ключ сервисного аккаунта Google и ID таблицы — см. [Настройка Google Sheets](google-sheets-setup.md).

## 2. Создание проекта

1. Перейдите на [railway.app](https://railway.app/) → **Sign up** (вход через GitHub).
2. Нажмите **New Project** → **Deploy from GitHub repo**.
3. Выберите репозиторий `dinary-server`.
4. Railway автоматически обнаружит Dockerfile и начнёт сборку.

## 3. Переменные окружения

В панели Railway → ваш сервис → **Variables**:

| Переменная | Значение |
|------------|----------|
| `DINARY_GOOGLE_SHEETS_SPREADSHEET_ID` | ID вашей таблицы |
| `DINARY_GOOGLE_SHEETS_CREDENTIALS_PATH` | `/app/credentials.json` |
| `DINARY_LOG_JSON` | `true` |

## 4. Добавление ключа сервисного аккаунта

Railway не имеет встроенной поддержки файлов-секретов. Варианты:

=== "Base64 в переменной (проще всего)"
    1. Закодируйте ключ: `base64 -i credentials.json`
    2. Добавьте переменную `DINARY_GOOGLE_CREDENTIALS_BASE64` с закодированным значением.
    3. dinary-server при запуске обнаружит эту переменную и запишет декодированный JSON по пути `DINARY_GOOGLE_SHEETS_CREDENTIALS_PATH` — изменения в коде не нужны.

=== "Монтирование тома"
    1. В панели Railway → сервис → **Volumes** → добавьте том, смонтированный в `/data`.
    2. Скопируйте файл через Railway CLI: `railway run cp credentials.json /data/`
    3. Установите `DINARY_GOOGLE_SHEETS_CREDENTIALS_PATH=/data/credentials.json`.

## 5. Настройка сети

1. В панели Railway → сервис → **Settings** → **Networking**.
2. Нажмите **Generate Domain** для получения URL вида `*.up.railway.app`.
3. Или добавьте свой домен: `dinary.yourdomain.com`.

## 6. Свой домен + Cloudflare Access

1. В Railway → **Settings** → **Custom Domain** → добавьте `dinary.yourdomain.com`.
2. В Cloudflare DNS добавьте CNAME: `dinary` → указанный Railway адрес (proxied).
3. Следуйте [инструкции по Cloudflare Access](cloudflare-setup.md#6-настройка-cloudflare-access) для аутентификации.

!!! note
    Как и Render, Railway предоставляет HTTPS — Cloudflare Tunnel не нужен. Cloudflare используется только для DNS + Access.

## Обслуживание

- **Логи**: панель Railway → сервис → **Logs** (в реальном времени).
- **Обновление**: push в GitHub — автодеплой.
- **Перезапуск**: панель → кнопка **Restart**.
- **Расход**: панель → вкладка **Usage** — вычисления/память/трафик.
