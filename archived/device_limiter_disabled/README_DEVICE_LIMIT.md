# Device Limit (server-side)

Этот модуль добавляет серверное ограничение количества одновременных устройств для VLESS/VMess по UUID подписки.

- Лимит хранится в PostgreSQL (`users.max_devices`, default `2`).
- UUID подписки хранится в `users.subscription_uuid`.
- Активные IP-сессии хранятся в `device_sessions`.
- Блокировка выполняется на сервере (`nftables`, fallback `iptables`).

## Что делает device_limitd

1. Читает `ACCESS_LOG_PATH` в режиме `tail -F`.
2. Парсит `uuid`, `client_ip`, `timestamp`.
3. Обновляет `device_sessions.last_seen`.
4. Считает активные IP по `TTL_SECONDS`.
5. Сравнивает с `users.max_devices` по `users.subscription_uuid`.
6. Если подключился новый IP и активных IP стало больше лимита, блокирует новый IP на `INBOUND_PORTS` до `BLOCK_TTL_SECONDS`.
7. Снимает просроченные блокировки по TTL.

## Переменные окружения

Обязательные:
- `DATABASE_URL` или `DEVICE_LIMIT_DATABASE_URL`
- `ACCESS_LOG_PATH`

Опциональные:
- `TTL_SECONDS=120`
- `BLOCK_TTL_SECONDS=120`
- `INBOUND_PORTS=443,8443`
- `BLOCK_UDP=true`
- `MAINTENANCE_INTERVAL_SECONDS=10`
- `LOG_POLL_INTERVAL=0.2`
- `DEVICE_LIMIT_LOG_LEVEL=INFO`
- `LOG_START_AT_END=true`

## Формат логов Xray/Hiddify

Парсер в `device_limit/parser.py` работает так:
- если строка JSON: `json.loads` + попытка найти uuid/ip/timestamp в распространенных полях;
- иначе regex-поиск UUID/IP по всей строке.

Если ваш формат access.log отличается, правьте маппинг в `device_limit/parser.py` (`_scan_json`).

## Миграции

Добавлена alembic-миграция `0006_device_limits.py`:
- `users.max_devices`
- `users.subscription_uuid` (+ unique index)
- `device_sessions`

Дополнительно `device_limitd` выполняет безопасный `ensure_schema` на старте (PostgreSQL `IF NOT EXISTS`).

## Админ-команды бота

Только для `ADMIN_IDS`:
- `/setlimit <uuid|user_id> <n>`
- `/getlimit <uuid|user_id>`
- `/whoonline <uuid|user_id>`
- `/resetdevices <uuid|user_id>`

`/resetdevices` удаляет записи из `device_sessions` и best-effort снимает firewall-блокировки по IP.

## Systemd

Файл юнита: `systemd/device-limit.service`.

Установка примером:

```bash
sudo cp systemd/device-limit.service /etc/systemd/system/device-limit.service
sudo systemctl daemon-reload
sudo systemctl enable --now device-limit.service
sudo systemctl status device-limit.service
```

Логи:

```bash
sudo journalctl -u device-limit.service -f
```

## Проверка MVP

1. У пользователя задан `subscription_uuid` и `max_devices=2`.
2. Два разных IP активны в пределах `TTL_SECONDS` -> доступ есть.
3. Третий IP приходит по тому же UUID -> `device_limitd` блокирует третий IP.
4. После истечения TTL блокировки или `/resetdevices` подключение снова возможно.
