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
Ты — senior Python dev. Твоя задача: внести изменения в МОЙ существующий проект Telegram-бота (не переписывать с нуля).

Контекст:
- В проекте есть Flask-сервис sub_combiner.py, который показывает страницу профиля подписки по эндпоинту:
  GET {SUB_DOMAIN}/user/<token>
  где token = users.subscription_token из SQLite vpn_bot.db (таблица users).
- Внутри user_page есть проверка "это Telegram?" и обход через query параметр tg=1.
  Поэтому ссылку из бота нужно формировать как:
  PROFILE_URL = f"{SUB_DOMAIN}/user/{user.subscription_token}?tg=1"

Что нужно сделать:
1) Найти в коде бота место, где подписка пользователя “изменилась”:
   - успешная оплата
   - продление / активация / смена плана
   - выдача/обновление subscription_url, uuid, UserServer и т.п.
   (это может быть handler оплаты, callback “оплатить”, postback от платежки, админская команда и т.д.)

2) После того как изменения подписки успешно применены (после commit транзакции/БД):
   - гарантировать, что у пользователя есть users.subscription_token
     - если None/пустой: сгенерировать безопасный токен (secrets.token_urlsafe(32)) и сохранить
   - сформировать ссылку на профиль:
     PROFILE_URL = f"{SUB_DOMAIN}/user/{user.subscription_token}?tg=1"
   - отправить пользователю сообщение в Telegram с этой ссылкой
     Текст: “Профиль подписки обновлён. Откройте профиль: <ссылка>”
     + InlineKeyboard с кнопкой:
       - “Открыть профиль подписки” -> URL = PROFILE_URL
     Дополнительно (если уместно) можно добавить кнопки:
       - “Подключить в Hiddify” -> f"{SUB_DOMAIN}/connect/{token}"
       - “Подключить в HAPP” -> f"{SUB_DOMAIN}/happ/{token}"
     но главное — ссылка на /user/<token>?tg=1.

3) Конфиг:
   - SUB_DOMAIN должен браться из env так же, как в sub_combiner.py (по умолчанию https://your-domain.com)
   - не хардкодить домен в нескольких местах: вынести в config/const как принято в проекте.

4) Надёжность:
   - отправку ссылки делать ТОЛЬКО если изменения подписки прошли успешно
   - при ошибке API/БД: не отправлять “успешно”, а показывать ошибку
   - если бот асинхронный: убедиться, что commit выполнен до отправки сообщения

5) Результат:
   - Выдай список файлов, которые меняешь
   - Дай конкретный дифф/патч или полный обновлённый код изменённых файлов
   - Опиши как протестировать:
     1) создать/обновить подписку
     2) получить сообщение со ссылкой
     3) открыть ссылку и убедиться, что страница профиля открывается (tg=1)

Важно:
- Не переписывай архитектуру.
- Следуй текущим библиотекам проекта (aiogram/pyTelegramBotAPI/etc).
- Не добавляй лишние зависимости без необходимости.