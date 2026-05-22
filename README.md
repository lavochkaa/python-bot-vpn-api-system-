# VPN Telegram Bot

Telegram bot for selling VPN subscriptions and managing keys.

## Stack
- Python 3.11+, aiogram v3
- PostgreSQL + SQLAlchemy 2 (async)
- Alembic for migrations
- pydantic-settings for config

## Quick start on server (copy-paste)

Commands for Ubuntu 22.04/24.04. Run in order.

### 1) First deploy

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip postgresql postgresql-contrib

sudo -u postgres psql -c "CREATE USER vpnbot WITH PASSWORD 'secret';" || true
sudo -u postgres psql -c "CREATE DATABASE vpnbot OWNER vpnbot;" || true
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE vpnbot TO vpnbot;" || true

mkdir -p ~/apps && cd ~/apps
git clone https://github.com/lavochkaa/python-bot-vpn-api-system-.git vpn-bot
cd vpn-bot

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
nano .env  # fill in BOT_TOKEN and other variables

alembic upgrade head
python3 -m bot.main
```

### 2) Auto-start with systemd

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

If the service didn't start, check logs:

```bash
journalctl -u vpn-bot.service -n 200 --no-pager
journalctl -u vpn-bot.service -f
```

### 3) Update on server

```bash
cd ~/apps/vpn-bot
git pull origin main
source .venv/bin/activate

alembic upgrade head
sudo systemctl restart vpn-bot.service
sudo systemctl status vpn-bot.service --no-pager
```

## Dev run (PostgreSQL)

```bash
cp .env.example .env
# Fill in BOT_TOKEN, VPN_CHANNEL_ID/USERNAME, PAYMENT_PROVIDER_TOKEN
pip install -r requirements.txt
alembic upgrade head
python3 -m bot.main
```

## Docker

```bash
cp .env.example .env
# Fill in BOT_TOKEN, VPN_CHANNEL_ID/USERNAME, PAYMENT_PROVIDER_TOKEN in .env
docker-compose up --build
```

## Migrations

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Architecture

```
handlers/     — UI layer, aiogram only + service calls
services/     — business logic
repositories/ — CRUD, isolated from business logic
providers/    — external integrations (payments, VPN keys)
states/       — FSM states
middlewares/  — DB session, channel subscription check
models.py     — ORM models
```

## Features

- ✅ Channel subscription check on /start
- ✅ Main menu with balance and subscription status
- ✅ Balance top-up with promo code
- ✅ Plan selection and switching (VPN / VPN+bypass)
- ✅ Automatic VPN key issuance after payment
- ✅ Key management in user cabinet
- ✅ Support system with ticket number
- ✅ "In development" section

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

## Common issues

- If `docker-compose` not found: use `docker compose ...` (no hyphen) or run locally without Docker.
- If `TelegramConflictError`: multiple bot instances running. Stop all processes and keep one.
