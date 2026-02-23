"""
Flask приложение для объединения подписок с нескольких Hiddify серверов
"""

import os
import base64
import requests
import re
from urllib.parse import quote
from flask import Flask, request, Response
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text, text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

app = Flask(__name__)

# Настройки
SUB_DOMAIN = os.getenv('SUB_DOMAIN', 'https://your-domain.com')
VPN_BRAND_NAME = os.getenv('VPN_BRAND_NAME', 'VPN')
VPN_SUPPORT_BOT = os.getenv('VPN_SUPPORT_BOT', 'support')
SUB_COMBINER_PORT = int(os.getenv('SUB_COMBINER_PORT', '5000'))

# База данных (та же что и у бота)
Base = declarative_base()


def _sync_db_url() -> str:
    raw = (os.getenv("DATABASE_URL") or "").strip()
    if not raw:
        return "sqlite:///vpn_bot.db"
    if raw.startswith("postgresql+asyncpg://"):
        return raw.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    return raw


engine = create_engine(_sync_db_url())
Session = sessionmaker(bind=engine)

# Используем те же модели что и в основном боте
class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    subscription_token = Column(String, unique=True)

class Server(Base):
    __tablename__ = 'servers'
    
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    url = Column(String, nullable=False)
    api_key = Column(String, nullable=False)
    proxy_path = Column(String, nullable=False)
    user_secret = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class UserServer(Base):
    __tablename__ = 'user_servers'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    server_id = Column(Integer, nullable=False)
    hiddify_uuid = Column(String, nullable=False)
    subscription_url = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class VpnKey(Base):
    __tablename__ = "vpn_keys"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    key = Column(Text, nullable=False)
    issued_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="active")


def _migrate_add_subscription_token():
    try:
        with engine.begin() as conn:
            dialect = conn.dialect.name
            if dialect == "sqlite":
                cols = conn.execute(text("PRAGMA table_info(users)")).fetchall()
                if cols:
                    col_names = {c[1] for c in cols}
                    if 'subscription_token' not in col_names:
                        conn.execute(text("ALTER TABLE users ADD COLUMN subscription_token VARCHAR"))
            elif dialect == "postgresql":
                conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_token VARCHAR"))
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_subscription_token ON users (subscription_token)"))
    except Exception as e:
        print(f"DB migration failed (add subscription_token): {e}")

    import secrets
    session = Session()
    try:
        users_without_token = session.query(User).filter(User.subscription_token.is_(None)).all()
        for u in users_without_token:
            u.subscription_token = secrets.token_urlsafe(32)
        if users_without_token:
            session.commit()
    except Exception as e:
        print(f"DB migration failed (fill subscription_token): {e}")
        session.rollback()
    finally:
        session.close()


_migrate_add_subscription_token()


def get_user_info_from_server(server: Server, uuid: str) -> dict:
    """Получает информацию о пользователе с сервера Hiddify"""
    try:
        headers = {
            'Hiddify-API-Key': server.api_key,
            'Accept': 'application/json'
        }
        response = requests.get(
            f'{server.url}/{server.proxy_path}/api/v2/admin/user/{uuid}/',
            headers=headers,
            timeout=10,
            verify=False
        )
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"Error getting user info from {server.name}: {e}")
    return None


def get_country_flag(server_name: str) -> str:
    """Возвращает флаг страны по названию сервера"""
    if not server_name:
        return '🌐'
    
    name_lower = server_name.lower()
    
    flags = {
        # Россия
        'росси': '🇷🇺', 'russia': '🇷🇺', 'ru': '🇷🇺', 'moscow': '🇷🇺', 'москв': '🇷🇺',
        # Нидерланды
        'нидерланд': '🇳🇱', 'netherlands': '🇳🇱', 'nl': '🇳🇱', 'holland': '🇳🇱', 'amsterdam': '🇳🇱',
        # Германия
        'герман': '🇩🇪', 'germany': '🇩🇪', 'de': '🇩🇪', 'frankfurt': '🇩🇪', 'berlin': '🇩🇪',
        # Франция
        'франц': '🇫🇷', 'france': '🇫🇷', 'fr': '🇫🇷', 'paris': '🇫🇷',
        # США
        'сша': '🇺🇸', 'usa': '🇺🇸', 'america': '🇺🇸', 'us': '🇺🇸', 'united states': '🇺🇸',
        'new york': '🇺🇸', 'los angeles': '🇺🇸', 'chicago': '🇺🇸', 'miami': '🇺🇸',
        # Великобритания
        'англи': '🇬🇧', 'британ': '🇬🇧', 'uk': '🇬🇧', 'england': '🇬🇧', 'britain': '🇬🇧', 'london': '🇬🇧',
        # Канада
        'канад': '🇨🇦', 'canada': '🇨🇦', 'ca': '🇨🇦', 'toronto': '🇨🇦',
        # Сингапур
        'сингапур': '🇸🇬', 'singapore': '🇸🇬', 'sg': '🇸🇬',
        # Япония
        'япон': '🇯🇵', 'japan': '🇯🇵', 'jp': '🇯🇵', 'tokyo': '🇯🇵',
        # Корея
        'коре': '🇰🇷', 'korea': '🇰🇷', 'kr': '🇰🇷', 'seoul': '🇰🇷',
        # Австралия
        'австрал': '🇦🇺', 'australia': '🇦🇺', 'au': '🇦🇺', 'sydney': '🇦🇺',
        # Бразилия
        'бразил': '🇧🇷', 'brazil': '🇧🇷', 'br': '🇧🇷',
        # Индия
        'инди': '🇮🇳', 'india': '🇮🇳', 'in': '🇮🇳', 'mumbai': '🇮🇳',
        # Турция
        'турц': '🇹🇷', 'турк': '🇹🇷', 'turkey': '🇹🇷', 'tr': '🇹🇷', 'istanbul': '🇹🇷',
        # Израиль
        'израил': '🇮🇱', 'israel': '🇮🇱', 'il': '🇮🇱',
        # ОАЭ
        'оаэ': '🇦🇪', 'эмират': '🇦🇪', 'uae': '🇦🇪', 'dubai': '🇦🇪',
        # Польша
        'польш': '🇵🇱', 'poland': '🇵🇱', 'pl': '🇵🇱', 'warsaw': '🇵🇱',
        # Финляндия
        'финлянд': '🇫🇮', 'finland': '🇫🇮', 'fi': '🇫🇮', 'helsinki': '🇫🇮',
        # Швеция
        'швец': '🇸🇪', 'sweden': '🇸🇪', 'se': '🇸🇪', 'stockholm': '🇸🇪',
        # Швейцария
        'швейцар': '🇨🇭', 'switzerland': '🇨🇭', 'swiss': '🇨🇭', 'ch': '🇨🇭', 'zurich': '🇨🇭',
        # Австрия
        'австри': '🇦🇹', 'austria': '🇦🇹', 'at': '🇦🇹', 'vienna': '🇦🇹',
        # Испания
        'испан': '🇪🇸', 'spain': '🇪🇸', 'es': '🇪🇸', 'madrid': '🇪🇸',
        # Италия
        'итал': '🇮🇹', 'italy': '🇮🇹', 'it': '🇮🇹', 'rome': '🇮🇹', 'milan': '🇮🇹',
        # Казахстан
        'казах': '🇰🇿', 'kazakh': '🇰🇿', 'kz': '🇰🇿',
        # Украина
        'украин': '🇺🇦', 'ukraine': '🇺🇦', 'ua': '🇺🇦', 'kiev': '🇺🇦',
        # Гонконг
        'гонконг': '🇭🇰', 'hong kong': '🇭🇰', 'hk': '🇭🇰',
        # Тайвань
        'тайван': '🇹🇼', 'taiwan': '🇹🇼', 'tw': '🇹🇼',
    }
    
    for key, flag in flags.items():
        if key in name_lower:
            return flag
    
    return '🌐'


def modify_config_name(config: str, server_name: str = None) -> str:
    """Изменяет название в конфиге"""
    from urllib.parse import quote, unquote
    
    if not server_name:
        return config
    
    try:
        if '#' in config:
            base_url, old_name = config.rsplit('#', 1)
            flag = get_country_flag(server_name)
            new_name = quote(f"{flag} {server_name}")
            return f"{base_url}#{new_name}"
    except:
        pass
    
    return config


def fetch_subscription_configs(subscription_url: str, server_name: str = None) -> list:
    """Загружает конфигурации с URL подписки"""
    configs = []
    user_agents = (
        "hiddify-next",
        "clash-meta",
        "v2rayN",
        "Streisand",
        "CrystalVPNCombiner/1.0",
    )
    url_variants = [
        subscription_url,
        f"{subscription_url}{'&' if '?' in subscription_url else '?'}sub=1",
        f"{subscription_url}{'&' if '?' in subscription_url else '?'}base64=1",
        f"{subscription_url}{'&' if '?' in subscription_url else '?'}raw=1",
    ]

    def _is_config_line(line: str) -> bool:
        return line.startswith(("vmess://", "vless://", "trojan://", "ss://", "ssr://", "hy2://", "tuic://"))

    def _decode_base64_payload(payload: str) -> str | None:
        compact = re.sub(r"\s+", "", payload)
        if not compact:
            return None
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                padded = compact + "=" * (-len(compact) % 4)
                decoded = decoder(padded.encode("utf-8")).decode("utf-8", errors="ignore")
                if "://" in decoded:
                    return decoded
            except Exception:
                continue
        return None

    for url in url_variants:
        for ua in user_agents:
            try:
                response = requests.get(
                    url,
                    timeout=12,
                    verify=False,
                    headers={
                        "User-Agent": ua,
                        "Accept": "text/plain,application/json,*/*",
                    },
                )
                if response.status_code >= 400:
                    continue
                body = response.text.strip()
                if not body:
                    continue
                if "<html" in body.lower():
                    continue

                lines = [line.strip() for line in body.split('\n') if line.strip()]
                raw_configs = [line for line in lines if _is_config_line(line)]
                if not raw_configs:
                    decoded = _decode_base64_payload(body)
                    if not decoded:
                        continue
                    decoded_lines = [line.strip() for line in decoded.split('\n') if line.strip()]
                    raw_configs = [line for line in decoded_lines if _is_config_line(line)]

                for line in raw_configs:
                    modified_line = modify_config_name(line, server_name)
                    configs.append(modified_line)
                if configs:
                    return configs
            except Exception as e:
                print(f"Error fetching subscription from {url} ua={ua}: {e}")
                continue
    return configs


def _rename_config_with_index(config: str, index: int) -> str:
    title = f"{VPN_BRAND_NAME} | обход {index}"
    try:
        if '#' in config:
            base_url, _ = config.rsplit('#', 1)
            return f"{base_url}#{quote(title)}"
    except Exception:
        pass
    return f"{config}#{quote(title)}"


def _table_exists(table_name: str) -> bool:
    try:
        with engine.begin() as conn:
            if conn.dialect.name == "sqlite":
                row = conn.execute(
                    text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name LIMIT 1"),
                    {"name": table_name},
                ).first()
                return bool(row)
            row = conn.execute(
                text("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=:name LIMIT 1"),
                {"name": table_name},
            ).first()
            return bool(row)
    except Exception:
        return False


def _load_configs_from_vpn_keys(session, user_id: int) -> list:
    try:
        key_row = (
            session.query(VpnKey)
            .filter_by(user_id=user_id)
            .order_by(VpnKey.issued_at.desc(), VpnKey.id.desc())
            .first()
        )
    except Exception:
        return []

    if not key_row or not key_row.key:
        return []
    key = key_row.key.strip()
    if not key:
        return []

    if key.startswith(("http://", "https://")):
        return fetch_subscription_configs(key, "Main")
    if _is_config_line(key):
        return [modify_config_name(key, "Main")]
    return []


@app.route('/sub')
def combine_subscriptions():
    """Объединяет подписки пользователя со всех серверов и возвращает base64"""
    token = request.args.get('token')
    
    if not token:
        return Response("Missing token parameter", status=400)
    
    session = Session()
    
    # Находим пользователя по токену подписки (не реферальному коду!)
    user = session.query(User).filter_by(subscription_token=token).first()
    
    if not user:
        session.close()
        return Response("Invalid token", status=404)
    
    all_configs = []

    # Legacy path (old schema with user_servers/servers).
    if _table_exists("user_servers") and _table_exists("servers"):
        try:
            user_servers = session.query(UserServer).filter_by(user_id=user.id).all()
            for us in user_servers:
                server = session.query(Server).filter_by(id=us.server_id).first()
                if server:
                    server_name = server.name
                    configs = fetch_subscription_configs(us.subscription_url, server_name)
                    all_configs.extend(configs)
        except Exception as e:
            print(f"Legacy user_servers path failed: {e}")

    # Current bot schema fallback: load latest key from vpn_keys.
    if not all_configs and _table_exists("vpn_keys"):
        all_configs.extend(_load_configs_from_vpn_keys(session, user.id))
    
    session.close()
    
    if not all_configs:
        return Response("No valid configurations found", status=404)
    
    # Переименование + дедупликация через новый модуль
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(__file__))
        from bot.utils.sub_combiner import rename_configs
        unique_configs = rename_configs(all_configs)
    except Exception as e:
        print(f"rename_configs failed in /sub, using raw: {e}")
        seen = set()
        unique_configs = []
        for config in all_configs:
            if config not in seen:
                seen.add(config)
                unique_configs.append(config)
    
    # Объединяем и кодируем обратно в base64
    combined = '\n'.join(unique_configs)
    encoded = base64.b64encode(combined.encode('utf-8')).decode('utf-8')
    
    # Возвращаем с заголовками
    response = Response(encoded, mimetype='text/plain')
    response.headers['profile-title'] = VPN_BRAND_NAME
    response.headers['subscription-userinfo'] = 'upload=0; download=0; total=0; expire=0'
    response.headers['profile-update-interval'] = '24'
    response.headers['support-url'] = f'https://t.me/{VPN_SUPPORT_BOT}'
    
    return response


@app.route('/sub_from_url')
def combine_subscription_from_url():
    """Перерабатывает подписку из внешнего URL и возвращает base64.

    Принимает параметр ``url`` (полностью закодированный, включая фрагмент #).
    Фрагмент игнорируется при скачивании — используется только путь+query.
    """
    from urllib.parse import unquote, urlsplit

    raw_url = request.args.get('url', '').strip()
    if not raw_url:
        # Обратная совместимость с параметром src
        raw_url = request.args.get('src', '').strip()
    if not raw_url:
        return Response("Missing url parameter", status=400)

    # Декодируем на случай двойного кодирования
    src = unquote(raw_url)

    # Убираем fragment (#…) — он не нужен при HTTP-запросе
    parsed = urlsplit(src)
    clean_src = parsed._replace(fragment="").geturl()

    configs = fetch_subscription_configs(clean_src, None)
    if not configs:
        return Response("No valid configurations found", status=404)

    # Применяем умное переименование через новый модуль
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(__file__))
        from bot.utils.sub_combiner import rename_configs
        renamed = rename_configs(configs)
    except Exception as e:
        print(f"rename_configs failed, falling back to index-based: {e}")
        renamed = []
        seen: set = set()
        for idx, cfg in enumerate(configs, start=1):
            mod = _rename_config_with_index(cfg, idx)
            if mod not in seen:
                seen.add(mod)
                renamed.append(mod)

    combined = '\n'.join(renamed)
    encoded = base64.b64encode(combined.encode('utf-8')).decode('utf-8')
    response = Response(encoded, mimetype='text/plain')
    response.headers['profile-title'] = VPN_BRAND_NAME
    response.headers['subscription-userinfo'] = 'upload=0; download=0; total=0; expire=0'
    response.headers['profile-update-interval'] = '24'
    response.headers['support-url'] = f'https://t.me/{VPN_SUPPORT_BOT}'
    return response


def render_redirect_page(token: str, brand_name: str, support_bot: str) -> str:
    """Показывает страницу с предложением открыть в Telegram"""
    return f'''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{brand_name} - Откройте в Telegram</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #0f0f23 0%, #1a1a2e 100%);
            color: #fff;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .container {{
            text-align: center;
            max-width: 400px;
        }}
        .icon {{
            font-size: 80px;
            margin-bottom: 24px;
            animation: bounce 2s ease-in-out infinite;
        }}
        @keyframes bounce {{
            0%, 100% {{ transform: translateY(0); }}
            50% {{ transform: translateY(-10px); }}
        }}
        h1 {{
            font-size: 24px;
            margin-bottom: 16px;
            background: linear-gradient(135deg, #6366f1, #8b5cf6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        p {{
            color: #a1a1aa;
            margin-bottom: 32px;
            line-height: 1.6;
        }}
        .btn {{
            display: inline-block;
            padding: 16px 32px;
            background: linear-gradient(135deg, #6366f1, #4f46e5);
            color: #fff;
            text-decoration: none;
            border-radius: 12px;
            font-weight: 600;
            font-size: 16px;
            transition: transform 0.2s, box-shadow 0.2s;
            box-shadow: 0 10px 30px rgba(99, 102, 241, 0.3);
        }}
        .btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 15px 40px rgba(99, 102, 241, 0.4);
        }}
        .footer {{
            margin-top: 32px;
            font-size: 14px;
            color: #71717a;
        }}
        .footer a {{
            color: #818cf8;
            text-decoration: none;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">📱</div>
        <h1>Откройте в Telegram</h1>
        <p>Эта страница доступна только через Telegram бот.<br>Нажмите кнопку ниже, чтобы перейти в бот.</p>
        <a href="https://t.me/{support_bot}" class="btn">Открыть в Telegram</a>
        <div class="footer">
            <p>Или напишите в <a href="https://t.me/{support_bot}">поддержку</a></p>
        </div>
    </div>
</body>
</html>
'''


@app.route('/connect/<token>')
def connect_hiddify(token):
    """Редирект в Hiddify с импортом подписки"""
    sub_url = f"{SUB_DOMAIN}/sub?token={token}"
    hiddify_deeplink = f"hiddify://install-sub?url={quote(sub_url, safe='')}"
    html = f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Подключение</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 24px; background: #0b0b0f; color: #fff; }}
        .card {{ max-width: 520px; margin: 0 auto; background: #11111a; border: 1px solid rgba(255,255,255,0.08); border-radius: 16px; padding: 20px; }}
        .btn {{ display:block; width:100%; padding: 14px 16px; border-radius: 12px; background: #4f46e5; color:#fff; text-decoration:none; text-align:center; font-weight: 600; }}
        .muted {{ color: rgba(255,255,255,0.7); font-size: 14px; line-height: 1.5; margin-top: 12px; }}
        input {{ width: 100%; padding: 12px; border-radius: 12px; border: 1px solid rgba(255,255,255,0.12); background: #0b0b0f; color: #fff; }}
    </style>
</head>
<body>
    <div class="card">
        <a class="btn" id="go">Открыть в Hiddify</a>
        <div class="muted">Если не открывается автоматически — нажмите кнопку ещё раз или вставьте ссылку подписки вручную:</div>
        <div style="margin-top:12px;"><input value="{sub_url}" readonly></div>
    </div>

    <script>
        const deeplink = {hiddify_deeplink!r};
        const go = document.getElementById('go');
        function openApp() {{
            try {{ window.location.href = deeplink; }} catch (e) {{}}
        }}
        go.addEventListener('click', function(e) {{ e.preventDefault(); openApp(); }});
        setTimeout(openApp, 50);
    </script>
</body>
</html>'''
    return html


@app.route('/happ/<token>')
def connect_happ(token):
    """Редирект в HAPP с импортом подписки"""
    sub_url = f"{SUB_DOMAIN}/sub?token={token}"
    happ_deeplink = f"happ://add/{quote(sub_url, safe='')}"
    html = f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Подключение</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 24px; background: #0b0b0f; color: #fff; }}
        .card {{ max-width: 520px; margin: 0 auto; background: #11111a; border: 1px solid rgba(255,255,255,0.08); border-radius: 16px; padding: 20px; }}
        .btn {{ display:block; width:100%; padding: 14px 16px; border-radius: 12px; background: #059669; color:#fff; text-decoration:none; text-align:center; font-weight: 600; }}
        .muted {{ color: rgba(255,255,255,0.7); font-size: 14px; line-height: 1.5; margin-top: 12px; }}
        input {{ width: 100%; padding: 12px; border-radius: 12px; border: 1px solid rgba(255,255,255,0.12); background: #0b0b0f; color: #fff; }}
    </style>
</head>
<body>
    <div class="card">
        <a class="btn" id="go">Открыть в HAPP</a>
        <div class="muted">Если не открывается автоматически — нажмите кнопку ещё раз или вставьте ссылку подписки вручную:</div>
        <div style="margin-top:12px;"><input value="{sub_url}" readonly></div>
    </div>

    <script>
        const deeplink = {happ_deeplink!r};
        const go = document.getElementById('go');
        function openApp() {{
            try {{ window.location.href = deeplink; }} catch (e) {{}}
        }}
        go.addEventListener('click', function(e) {{ e.preventDefault(); openApp(); }});
        setTimeout(openApp, 50);
    </script>
</body>
</html>'''
    return html


@app.route('/url/')
def url_redirect():
    """Универсальный редирект на любой URL (для deeplink через HTTPS)"""
    target_url = request.args.get('url', '')
    if not target_url:
        return "Missing url parameter", 400
    
    html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="0;url={target_url}">
    <script>window.location.href = {target_url!r};</script>
</head>
<body>
    <p>Redirecting...</p>
    <a href="{target_url}">Click here if not redirected</a>
</body>
</html>'''
    return html


@app.route('/user/<token>')
def user_page(token):
    """Показывает страницу пользователя со всеми серверами и кнопкой подключения"""
    # Проверяем, что страница открыта в Telegram (встроенный браузер или WebApp)
    user_agent = request.headers.get('User-Agent', '').lower()
    is_telegram = (
        'telegram' in user_agent or  # Встроенный браузер Telegram
        request.args.get('tg') == '1' or  # Явный флаг от бота
        request.args.get('tgWebAppStartParam') or  # WebApp параметр
        request.headers.get('X-Telegram-Web-App')  # WebApp заголовок
    )
    
    session = Session()
    # Используем subscription_token для безопасности (не referral_code!)
    user = session.query(User).filter_by(subscription_token=token).first()
    
    if not user:
        session.close()
        return "Invalid token", 404
    
    # Получаем все подписки пользователя (legacy schema)
    user_servers = []
    if _table_exists("user_servers"):
        try:
            user_servers = session.query(UserServer).filter_by(user_id=user.id).all()
        except Exception as e:
            print(f"Failed to load user_servers: {e}")
            user_servers = []
    
    # Собираем информацию о серверах
    servers_info = []
    processed_server_ids = set()
    total_traffic_used = 0
    total_traffic_limit = 0
    min_remaining_days = float('inf')
    
    # Обрабатываем UserServer (основной источник)
    for us in user_servers:
        server = session.query(Server).filter_by(id=us.server_id).first()
        if server and server.id not in processed_server_ids:
            processed_server_ids.add(server.id)
            info = get_user_info_from_server(server, us.hiddify_uuid)
            if info:
                used = info.get('current_usage_GB', 0)
                limit = info.get('usage_limit_GB', 0)
                days = info.get('package_days', 0)
                
                remaining = days
                if info.get('start_date'):
                    try:
                        start = datetime.fromisoformat(info['start_date'].replace('Z', '+00:00'))
                        passed = (datetime.now() - start.replace(tzinfo=None)).days
                        remaining = max(0, days - passed)
                    except:
                        pass
                
                servers_info.append({
                    'name': server.name,
                    'used_gb': used,
                    'limit_gb': limit,
                    'remaining_days': remaining,
                    'is_active': info.get('enable', False)
                })
                
                total_traffic_used += used
                if limit < 999999:
                    total_traffic_limit += limit
                else:
                    total_traffic_limit = float('inf')
                    
                if remaining < min_remaining_days:
                    min_remaining_days = remaining
    
    session.close()
    
    # Генерируем ссылки
    sub_url = f"{SUB_DOMAIN}/sub?token={token}"
    connect_url = f"{SUB_DOMAIN}/connect/{token}"
    happ_url = f"{SUB_DOMAIN}/happ/{token}"

    # Deep links для Hiddify и HAPP через URL-редирект (обход блокировки deeplink в Telegram WebView)
    hiddify_raw = f"hiddify://import/{sub_url}"
    happ_raw = f"happ://add/{sub_url}"
    hiddify_deeplink = f"{SUB_DOMAIN}/url/?url={quote(hiddify_raw, safe='')}"
    happ_deeplink = f"{SUB_DOMAIN}/url/?url={quote(happ_raw, safe='')}"
    
    # Если не в Telegram - показываем страницу с редиректом
    if not is_telegram:
        return render_redirect_page(token, VPN_BRAND_NAME, VPN_SUPPORT_BOT)
    
    # Форматируем трафик
    if total_traffic_limit == float('inf'):
        traffic_text = f"{total_traffic_used:.1f} ГБ / ∞"
        traffic_remaining = "∞"
    else:
        traffic_remaining = f"{max(0, total_traffic_limit - total_traffic_used):.1f} ГБ"
        traffic_text = f"{total_traffic_used:.1f} ГБ / {total_traffic_limit:.0f} ГБ"
    
    if min_remaining_days == float('inf'):
        days_text = "∞"
    else:
        days_text = f"{int(min_remaining_days)} дней"
    
    # Генерируем HTML для списка серверов
    servers_html = ""
    for i, srv in enumerate(servers_info):
        status_class = "status-active" if srv['is_active'] else "status-inactive"
        status_text = "Активен" if srv['is_active'] else "Неактивен"
        server_flag = get_country_flag(srv['name'])
        
        if srv['limit_gb'] >= 999999:
            srv_traffic = f"{srv['used_gb']:.1f} ГБ / ∞"
        else:
            srv_traffic = f"{srv['used_gb']:.1f} / {srv['limit_gb']:.0f} ГБ"
        
        servers_html += f'''
        <div class="server-card" style="animation-delay: {i * 0.1}s">
            <div class="server-header">
                <div class="server-icon">{server_flag}</div>
                <div class="server-name">{srv['name']}</div>
                <div class="{status_class}">{status_text}</div>
            </div>
            <div class="server-stats">
                <div class="stat">
                    <span class="stat-label">Трафик</span>
                    <span class="stat-value">{srv_traffic}</span>
                </div>
                <div class="stat">
                    <span class="stat-label">Осталось</span>
                    <span class="stat-value">{srv['remaining_days']} дней</span>
                </div>
            </div>
        </div>
        '''
    
    if not servers_info:
        servers_html = '''
        <div class="no-servers">
            <div class="no-servers-icon">📡</div>
            <p>Нет активных серверов</p>
        </div>
        '''
    
    html = f'''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>VPN Подписка</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --primary: #6366f1;
            --primary-dark: #4f46e5;
            --primary-light: #818cf8;
            --bg-dark: #0f0f23;
            --bg-card: #1a1a2e;
            --bg-card-hover: #252542;
            --text-primary: #ffffff;
            --text-secondary: #a1a1aa;
            --text-muted: #71717a;
            --success: #22c55e;
            --warning: #f59e0b;
            --danger: #ef4444;
            --border: rgba(255,255,255,0.1);
        }}
        
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg-dark);
            color: var(--text-primary);
            min-height: 100vh;
            overflow-x: hidden;
        }}
        
        /* Animated background */
        .bg-animation {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            overflow: hidden;
            z-index: -1;
        }}
        
        .bg-animation::before {{
            content: '';
            position: absolute;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: 
                radial-gradient(circle at 20% 80%, rgba(99, 102, 241, 0.15) 0%, transparent 50%),
                radial-gradient(circle at 80% 20%, rgba(139, 92, 246, 0.1) 0%, transparent 50%),
                radial-gradient(circle at 40% 40%, rgba(59, 130, 246, 0.08) 0%, transparent 50%);
            animation: bgMove 20s ease-in-out infinite;
        }}
        
        @keyframes bgMove {{
            0%, 100% {{ transform: translate(0, 0) rotate(0deg); }}
            33% {{ transform: translate(2%, 2%) rotate(1deg); }}
            66% {{ transform: translate(-1%, 1%) rotate(-1deg); }}
        }}
        
        .container {{
            max-width: 480px;
            margin: 0 auto;
            padding: 20px;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }}
        
        /* Header */
        .header {{
            text-align: center;
            padding: 30px 0;
            animation: fadeInDown 0.6s ease-out;
        }}
        
        @keyframes fadeInDown {{
            from {{
                opacity: 0;
                transform: translateY(-20px);
            }}
            to {{
                opacity: 1;
                transform: translateY(0);
            }}
        }}
        
        .logo {{
            width: 80px;
            height: 80px;
            background: linear-gradient(135deg, var(--primary), var(--primary-dark));
            border-radius: 24px;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 16px;
            font-size: 40px;
            box-shadow: 0 20px 40px rgba(99, 102, 241, 0.3);
            animation: pulse 2s ease-in-out infinite;
        }}
        
        @keyframes pulse {{
            0%, 100% {{ transform: scale(1); box-shadow: 0 20px 40px rgba(99, 102, 241, 0.3); }}
            50% {{ transform: scale(1.02); box-shadow: 0 25px 50px rgba(99, 102, 241, 0.4); }}
        }}
        
        .header h1 {{
            font-size: 28px;
            font-weight: 700;
            margin-bottom: 8px;
            background: linear-gradient(135deg, #fff, #a1a1aa);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        
        .header p {{
            color: var(--text-secondary);
            font-size: 14px;
        }}
        
        /* Stats Cards */
        .stats-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            margin-bottom: 24px;
            animation: fadeInUp 0.6s ease-out 0.2s both;
        }}
        
        @keyframes fadeInUp {{
            from {{
                opacity: 0;
                transform: translateY(20px);
            }}
            to {{
                opacity: 1;
                transform: translateY(0);
            }}
        }}
        
        .stat-card {{
            background: var(--bg-card);
            border-radius: 16px;
            padding: 20px;
            border: 1px solid var(--border);
            transition: all 0.3s ease;
        }}
        
        .stat-card:hover {{
            background: var(--bg-card-hover);
            transform: translateY(-2px);
        }}
        
        .stat-card .icon {{
            font-size: 24px;
            margin-bottom: 12px;
        }}
        
        .stat-card .label {{
            font-size: 12px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 4px;
        }}
        
        .stat-card .value {{
            font-size: 20px;
            font-weight: 600;
            color: var(--text-primary);
        }}
        
        /* Servers Section */
        .servers-section {{
            flex: 1;
            animation: fadeInUp 0.6s ease-out 0.4s both;
        }}
        
        .section-title {{
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        
        .section-title .count {{
            background: var(--primary);
            color: white;
            font-size: 12px;
            padding: 2px 8px;
            border-radius: 10px;
        }}
        
        .servers-list {{
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}
        
        .server-card {{
            background: var(--bg-card);
            border-radius: 16px;
            padding: 16px;
            border: 1px solid var(--border);
            animation: fadeInUp 0.4s ease-out both;
            transition: all 0.3s ease;
        }}
        
        .server-card:hover {{
            background: var(--bg-card-hover);
            border-color: var(--primary);
        }}
        
        .server-header {{
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 12px;
        }}
        
        .server-icon {{
            font-size: 24px;
        }}
        
        .server-name {{
            flex: 1;
            font-weight: 600;
            font-size: 16px;
        }}
        
        .status-active {{
            background: rgba(34, 197, 94, 0.2);
            color: var(--success);
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 500;
        }}
        
        .status-inactive {{
            background: rgba(239, 68, 68, 0.2);
            color: var(--danger);
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 500;
        }}
        
        .server-stats {{
            display: flex;
            gap: 24px;
        }}
        
        .server-stats .stat {{
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}
        
        .server-stats .stat-label {{
            font-size: 11px;
            color: var(--text-muted);
            text-transform: uppercase;
        }}
        
        .server-stats .stat-value {{
            font-size: 14px;
            font-weight: 500;
            color: var(--text-secondary);
        }}
        
        .no-servers {{
            text-align: center;
            padding: 40px 20px;
            color: var(--text-muted);
        }}
        
        .no-servers-icon {{
            font-size: 48px;
            margin-bottom: 12px;
            opacity: 0.5;
        }}
        
        /* Connect Button */
        .connect-section {{
            padding: 24px 0;
            animation: fadeInUp 0.6s ease-out 0.6s both;
        }}
        
        .connect-btn {{
            width: 100%;
            padding: 18px 24px;
            background: linear-gradient(135deg, var(--primary), var(--primary-dark));
            color: white;
            border: none;
            border-radius: 16px;
            font-size: 18px;
            font-weight: 600;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            text-decoration: none;
            transition: all 0.3s ease;
            box-shadow: 0 10px 30px rgba(99, 102, 241, 0.3);
        }}
        
        .connect-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 15px 40px rgba(99, 102, 241, 0.4);
        }}
        
        .connect-btn:active {{
            transform: translateY(0);
        }}
        
        .connect-btn .icon {{
            font-size: 24px;
        }}
        
        /* Subscription Link */
        .sub-link-section {{
            margin-top: 16px;
        }}
        
        .sub-link-card {{
            background: var(--bg-card);
            border-radius: 12px;
            padding: 16px;
            border: 1px solid var(--border);
        }}
        
        .sub-link-label {{
            font-size: 12px;
            color: var(--text-muted);
            margin-bottom: 8px;
        }}
        
        .sub-link-input {{
            display: flex;
            gap: 8px;
        }}
        
        .sub-link-input input {{
            flex: 1;
            background: var(--bg-dark);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 12px;
            color: var(--text-primary);
            font-size: 13px;
            font-family: monospace;
        }}
        
        .copy-btn {{
            background: var(--bg-dark);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 12px 16px;
            color: var(--text-primary);
            cursor: pointer;
            transition: all 0.2s ease;
            font-size: 16px;
        }}
        
        .copy-btn:hover {{
            background: var(--primary);
            border-color: var(--primary);
        }}
        
        .copy-btn.copied {{
            background: var(--success);
            border-color: var(--success);
        }}
        
        /* Footer */
        .footer {{
            text-align: center;
            padding: 20px 0;
            color: var(--text-muted);
            font-size: 12px;
        }}
        
        .footer a {{
            color: var(--primary-light);
            text-decoration: none;
        }}
        
        /* Mobile optimizations */
        @media (max-width: 400px) {{
            .container {{
                padding: 16px;
            }}
            
            .header h1 {{
                font-size: 24px;
            }}
            
            .stat-card .value {{
                font-size: 18px;
            }}
        }}
    </style>
</head>
<body>
    <div class="bg-animation"></div>
    
    <div class="container">
        <header class="header">
            <div class="logo">🔐</div>
            <h1>{VPN_BRAND_NAME}</h1>
            <p>Ваша персональная конфигурация</p>
        </header>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="icon">📊</div>
                <div class="label">Остаток трафика</div>
                <div class="value">{traffic_remaining}</div>
            </div>
            <div class="stat-card">
                <div class="icon">⏰</div>
                <div class="label">Осталось дней</div>
                <div class="value">{days_text}</div>
            </div>
        </div>
        
        <section class="servers-section">
            <h2 class="section-title">
                📡 Серверы 
                <span class="count">{len(servers_info)}</span>
            </h2>
            <div class="servers-list">
                {servers_html}
            </div>
        </section>
        
        <section class="connect-section">
            <a href="{hiddify_deeplink}" class="connect-btn" id="connectBtn" onclick="openDeeplink('{hiddify_deeplink}', event)">
                <span class="icon">⚡</span>
                Открыть в Hiddify
            </a>

            <a href="{happ_deeplink}" class="connect-btn" style="margin-top: 12px; background: linear-gradient(135deg, #10b981, #059669);" id="happBtn" onclick="openDeeplink('{happ_deeplink}', event)">
                <span class="icon">📱</span>
                Открыть в HAPP
            </a>
            
            <div class="sub-link-section">
                <div class="sub-link-card">
                    <div class="sub-link-label">Ссылка подписки (скопируйте и вставьте в приложение)</div>
                    <div class="sub-link-input">
                        <input type="text" value="{sub_url}" readonly id="subLink">
                        <button class="copy-btn" onclick="copyLink()" id="copyBtn">📋</button>
                    </div>
                    <p style="color: var(--text-muted); font-size: 12px; margin-top: 8px; text-align: center;">
                        Если кнопка не работает — скопируйте ссылку и добавьте вручную в приложении
                    </p>
                </div>
            </div>
        </section>
        
        <footer class="footer">
            <p>Нужна помощь? <a href="https://t.me/{VPN_SUPPORT_BOT}">Напишите в поддержку</a></p>
        </footer>
    </div>
    
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script>
        function openDeeplink(url, event) {{
            event.preventDefault();
            
            // Пробуем через Telegram WebApp API
            if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.openLink) {{
                try {{
                    window.Telegram.WebApp.openLink(url);
                    return;
                }} catch (e) {{
                    console.log('WebApp.openLink failed:', e);
                }}
            }}
            
            // Fallback: обычный редирект
            window.location.href = url;
        }}
        
        function copyLink() {{
            const input = document.getElementById('subLink');
            const btn = document.getElementById('copyBtn');
            const text = input.value;
            
            function onSuccess() {{
                btn.textContent = '✓';
                btn.classList.add('copied');
                if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.HapticFeedback) {{
                    window.Telegram.WebApp.HapticFeedback.notificationOccurred('success');
                }}
                setTimeout(() => {{
                    btn.textContent = '📋';
                    btn.classList.remove('copied');
                }}, 2000);
            }}
            
            // Выделяем текст в input
            input.focus();
            input.select();
            input.setSelectionRange(0, 99999);
            
            let copied = false;
            
            // Пробуем execCommand
            try {{
                copied = document.execCommand('copy');
            }} catch (e) {{}}
            
            // Если не сработало, пробуем clipboard API
            if (!copied && navigator.clipboard && navigator.clipboard.writeText) {{
                navigator.clipboard.writeText(text).then(() => {{
                    onSuccess();
                }}).catch(() => {{
                    showCopyHint(text);
                }});
                input.blur();
                return;
            }}
            
            input.blur();
            
            if (copied) {{
                onSuccess();
            }} else {{
                showCopyHint(text);
            }}
        }}
        
        function showCopyHint(text) {{
            if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.showAlert) {{
                window.Telegram.WebApp.showAlert('Удерживайте поле со ссылкой для копирования');
            }} else {{
                alert('Удерживайте поле со ссылкой для копирования');
            }}
        }}
        
        // Detect device and show appropriate message
        const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
        
        if (!isMobile) {{
            // On desktop, show hint (deeplinks won't work)
            const connectBtn = document.getElementById('connectBtn');
            if (connectBtn) {{
                connectBtn.innerHTML = '<span class="icon">📱</span> Откройте на телефоне для подключения';
                connectBtn.href = '#';
                connectBtn.onclick = function(e) {{
                    e.preventDefault();
                    alert('Для подключения откройте эту страницу на телефоне с установленным приложением');
                }};
            }}
        }}

    </script>
</body>
</html>
'''
    
    return html


@app.route('/health')
def health():
    """Health check endpoint"""
    return {'status': 'ok'}


if __name__ == '__main__':
    import warnings
    import urllib3
    warnings.filterwarnings('ignore')
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    app.run(host='0.0.0.0', port=SUB_COMBINER_PORT, debug=False)
