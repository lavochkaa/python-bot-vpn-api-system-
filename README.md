# VPN Telegram Bot

Telegram-бот для продажи VPN-подписок и управления ключами.

## Стек
- Python 3.11+, aiogram v3
- PostgreSQL + SQLAlchemy 2 (async)
- Alembic для миграций
- pydantic-settings для конфига

## Запуск (dev, PostgreSQL)

```bash
cp .env.example .env
# Заполни BOT_TOKEN, VPN_CHANNEL_ID/USERNAME, PAYMENT_PROVIDER_TOKEN
pip install -r requirements.txt
alembic upgrade head
python -m bot.main
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

## TODO для завершения (Codex)

- [ ] `PaymentService.confirm_payment`: найти платёж по invoice_id, обновить статус, пополнить баланс
- [ ] Реальный `PaymentProvider` (Telegram Payments / YooKassa / Cryptomus)
- [ ] Реальный `VpnKeyProvider` (Outline API / 3x-ui API)
- [ ] Admin-панель: управление промокодами, тарифами, тикетами
- [ ] Webhook-режим вместо polling для продакшена
- [ ] Тесты (pytest + pytest-asyncio)
