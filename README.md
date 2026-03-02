# VPN Telegram Bot

Telegram-бот для продажи VPN-подписок и управления ключами.

## Стек
- Python 3.11+, aiogram v3
- PostgreSQL + SQLAlchemy 2 (async)
- Alembic для миграций
- pydantic-settings для конфига

## Быстрый запуск на сервере (copy-paste)

Ниже команды для Ubuntu 22.04/24.04. Выполняй по порядку.

### 1) Первый деплой

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip postgresql postgresql-contrib

sudo -u postgres psql -c "CREATE USER vpnbot WITH PASSWORD 'secret';" || true
sudo -u postgres psql -c "CREATE DATABASE vpnbot OWNER vpnbot;" || true
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE vpnbot TO vpnbot;" || true

mkdir -p ~/apps && cd ~/apps
git clone https://github.com/lavochkaa/vpn-bot-for-roma.git vpn-bot
cd vpn-bot

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
echo "Открой .env и заполни BOT_TOKEN + остальные переменные"
nano .env

alembic upgrade head
python3 -m bot.main
```

### 2) Автозапуск через systemd

```bash
sudo tee /etc/systemd/system/vpn-bot.service >/dev/null <<'EOF'
[Unit]
Description=VPN Telegram Bot
After=network.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/apps/vpn-bot
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/YOUR_USER/apps/vpn-bot/.venv/bin/python3 -m bot.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable vpn-bot.service
sudo systemctl start vpn-bot.service
sudo systemctl status vpn-bot.service --no-pager
```

Если сервис не стартовал, смотри логи:

```bash
journalctl -u vpn-bot.service -n 200 --no-pager
journalctl -u vpn-bot.service -f
```

### 3) Обновление на сервере

```bash
cd ~/apps/vpn-bot
git pull origin main
source .venv/bin/activate


alembic upgrade head
sudo systemctl restart vpn-bot.service
sudo systemctl status vpn-bot.service --no-pager
```

## Запуск (dev, PostgreSQL)

```bash
cp .env.example .env
# Заполни BOT_TOKEN, VPN_CHANNEL_ID/USERNAME, PAYMENT_PROVIDER_TOKEN
pip install -r requirements.txt
alembic upgrade head
python3 -m bot.main
```

## Запуск через Docker

```bash
cp .env.example .env
# Заполни BOT_TOKEN, VPN_CHANNEL_ID/USERNAME, PAYMENT_PROVIDER_TOKEN в .env
docker-compose up --build
```

## Миграции

```bash
alembic revision --autogenerate -m "описание"
alembic upgrade head
```

## Архитектура

```
handlers/     — UI-слой, только aiogram + вызов сервисов
services/     — бизнес-логика
repositories/ — CRUD, изолирован от бизнес-логики
providers/    — внешние интеграции (платежи, VPN-ключи)
states/       — FSM-состояния
middlewares/  — DB-сессия, проверка подписки на канал
models.py     — ORM-модели
```

## Функциональность

- ✅ Проверка подписки на канал при /start
- ✅ Главное меню с балансом и статусом подписки
- ✅ Пополнение баланса с промокодом
- ✅ Выбор и смена тарифа (VPN / VPN+обход)
- ✅ Автовыдача VPN-ключей после оплаты
- ✅ Просмотр ключей в личном кабинете
- ✅ Система поддержки с номером тикета
- ✅ Раздел "в разработке"

## VPN API Integration

```env
VPN_PROVIDER=api
VPN_API_BASE_URL=https://vpn-provider.example/api
VPN_API_KEY=your_api_key
VPN_API_TIMEOUT_SECONDS=20
VPN_API_VERIFY_SSL=true
```

Expected endpoints:
- `POST /subscriptions` -> create subscription
- `POST /subscriptions/{id}/extend` -> extend subscription
- `GET /subscriptions/{id}` -> read subscription

## Частые проблемы

- Если `docker-compose` не найден: используй `docker compose ...` (без дефиса) или запускай локально без Docker.
- Если `TelegramConflictError`: запущено несколько экземпляров бота. Заверши все процессы и оставь один.
