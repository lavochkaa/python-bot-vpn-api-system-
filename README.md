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
python3 -m bot.main
```

## Запуск через Docker

```bash
cp .env.example .env
# Заполни BOT_TOKEN, VPN_CHANNEL_ID/USERNAME, PAYMENT_PROVIDER_TOKEN в .env
docker-compose up --build
```

## Sub Combiner (отдельный сервис)

В проект импортирован полный `sub_combiner.py` (Flask-сервис для объединения/модификации подписок).

Запуск вместе с ботом через Docker:

```bash
docker-compose up --build
```

Сервис доступен на `http://localhost:5000`.
Чтобы бот автоматически выдавал ссылку через combiner после оплаты, укажите в `.env`:

```env
SUB_COMBINER_BASE_URL=https://your-domain.com
# или как минимум
SUB_DOMAIN=https://your-domain.com
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

## Практические Подсказки (Для Себя)

- Если `docker-compose` не найден: используй `docker compose ...` (без дефиса) или запускай локально без Docker.
- Если `TelegramConflictError`: запущено несколько экземпляров бота. Заверши все процессы и оставь один.
- Если `Port 5000 is in use`: запусти `sub_combiner` на другом порту (например `5001`) и обнови `SUB_COMBINER_BASE_URL`.
- Для переработки подписки через combiner в `.env` обязательно:
  - `VPN_PROVIDER=hiddify`
  - `SUB_COMBINER_BASE_URL=https://<домен-или-ip>`
  - `SUB_DOMAIN=https://<домен-или-ip>`
- Если ссылка из бота не меняется:
  - проверь, что `sub_combiner.py` реально запущен и доступен по URL;
  - проверь, что бот перезапущен после изменения `.env`.

## Что Доделать

- [ ] Привести `sub_combiner.py` к текущей БД проекта (сейчас в нем legacy-модели из другого проекта).
- [ ] Добавить нормальный health-check и логирование причин fallback в `HiddifyVpnKeyProvider`.
- [ ] Добавить интеграционные тесты на флоу покупки и выдачи ссылки.
- [ ] Убрать дублирующиеся/устаревшие сценарии подключения и оставить один основной.
- [ ] Добавить команду админа для проверки текущей конфигурации env (без секретов).

## Команды Для Commit + Push В main

```bash
git add .
git commit -m "Add sub combiner integration, subscription link flow fixes, and README operational notes"
git push origin main
```
