"""
Утилита переименования и дедупликации VPN-конфигов.

Формат имён узлов:
  - обычный:  "ГЕРМАНИЯ VLESS 🇩🇪"
  - повтор:   "ГЕРМАНИЯ VLESS №2 🇩🇪"
  - обход:    "ОБХОД ГЛУШИЛОК №1 🇷🇺"
"""

import base64
import json
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, unquote

# ── Константы ──────────────────────────────────────────────────────────────────

SUPPORTED_SCHEMES = ("vmess://", "vless://", "trojan://", "ss://", "ssr://", "hy2://", "tuic://")

# Показывать №1 или нет (можно переопределить через env)
SHOW_FIRST_NUMBER = False

# Шаблоны имён
TEMPLATE_NORMAL = "{country} {proto} {flag}"
TEMPLATE_NUMBERED = "{country} {proto} №{n} {flag}"
TEMPLATE_BYPASS = "ОБХОД ГЛУШИЛОК №{n} 🇷🇺"

# Ключевые слова для определения "обхода глушилок"
BYPASS_KEYWORDS = (
    "bypass", "obhod", "обход", "anti-block", "antiblock",
    "глушилок", "unblock", "unblocker", "anti_block",
)

# Словарь алиасов стран: подстрока (lower) → (country_upper, flag)
# Порядок важен: более специфичные строки раньше
COUNTRY_MAP: List[Tuple[str, str, str]] = [
    # Германия
    ("герман",      "ГЕРМАНИЯ",      "🇩🇪"),
    ("germany",     "ГЕРМАНИЯ",      "🇩🇪"),
    ("frankfurt",   "ГЕРМАНИЯ",      "🇩🇪"),
    ("berlin",      "ГЕРМАНИЯ",      "🇩🇪"),
    ("-de.",        "ГЕРМАНИЯ",      "🇩🇪"),
    (".de.",        "ГЕРМАНИЯ",      "🇩🇪"),
    # Нидерланды
    ("нидерланд",   "НИДЕРЛАНДЫ",    "🇳🇱"),
    ("netherlands", "НИДЕРЛАНДЫ",    "🇳🇱"),
    ("holland",     "НИДЕРЛАНДЫ",    "🇳🇱"),
    ("amsterdam",   "НИДЕРЛАНДЫ",    "🇳🇱"),
    ("-nl.",        "НИДЕРЛАНДЫ",    "🇳🇱"),
    (".nl.",        "НИДЕРЛАНДЫ",    "🇳🇱"),
    # Польша
    ("польш",       "ПОЛЬША",        "🇵🇱"),
    ("poland",      "ПОЛЬША",        "🇵🇱"),
    ("warsaw",      "ПОЛЬША",        "🇵🇱"),
    ("-pl.",        "ПОЛЬША",        "🇵🇱"),
    (".pl.",        "ПОЛЬША",        "🇵🇱"),
    # Россия
    ("росси",       "РОССИЯ",        "🇷🇺"),
    ("russia",      "РОССИЯ",        "🇷🇺"),
    ("moscow",      "РОССИЯ",        "🇷🇺"),
    ("москв",       "РОССИЯ",        "🇷🇺"),
    ("-ru.",        "РОССИЯ",        "🇷🇺"),
    (".ru.",        "РОССИЯ",        "🇷🇺"),
    # США
    ("сша",         "США",           "🇺🇸"),
    ("usa",         "США",           "🇺🇸"),
    ("america",     "США",           "🇺🇸"),
    ("new york",    "США",           "🇺🇸"),
    ("los angeles", "США",           "🇺🇸"),
    # Франция
    ("франц",       "ФРАНЦИЯ",       "🇫🇷"),
    ("france",      "ФРАНЦИЯ",       "🇫🇷"),
    ("paris",       "ФРАНЦИЯ",       "🇫🇷"),
    ("-fr.",        "ФРАНЦИЯ",       "🇫🇷"),
    # Великобритания
    ("англи",       "ВЕЛИКОБРИТАНИЯ","🇬🇧"),
    ("британ",      "ВЕЛИКОБРИТАНИЯ","🇬🇧"),
    ("england",     "ВЕЛИКОБРИТАНИЯ","🇬🇧"),
    ("london",      "ВЕЛИКОБРИТАНИЯ","🇬🇧"),
    ("britain",     "ВЕЛИКОБРИТАНИЯ","🇬🇧"),
    # Финляндия
    ("финлянд",     "ФИНЛЯНДИЯ",     "🇫🇮"),
    ("finland",     "ФИНЛЯНДИЯ",     "🇫🇮"),
    ("helsinki",    "ФИНЛЯНДИЯ",     "🇫🇮"),
    # Швеция
    ("швец",        "ШВЕЦИЯ",        "🇸🇪"),
    ("sweden",      "ШВЕЦИЯ",        "🇸🇪"),
    ("stockholm",   "ШВЕЦИЯ",        "🇸🇪"),
    # Турция
    ("турц",        "ТУРЦИЯ",        "🇹🇷"),
    ("турк",        "ТУРЦИЯ",        "🇹🇷"),
    ("turkey",      "ТУРЦИЯ",        "🇹🇷"),
    ("istanbul",    "ТУРЦИЯ",        "🇹🇷"),
    # Сингапур
    ("сингапур",    "СИНГАПУР",      "🇸🇬"),
    ("singapore",   "СИНГАПУР",      "🇸🇬"),
    # Япония
    ("япон",        "ЯПОНИЯ",        "🇯🇵"),
    ("japan",       "ЯПОНИЯ",        "🇯🇵"),
    ("tokyo",       "ЯПОНИЯ",        "🇯🇵"),
    # Швейцария
    ("швейцар",     "ШВЕЙЦАРИЯ",     "🇨🇭"),
    ("switzerland", "ШВЕЙЦАРИЯ",     "🇨🇭"),
    ("zurich",      "ШВЕЙЦАРИЯ",     "🇨🇭"),
    # Нидерланды доп.
    ("-nl-",        "НИДЕРЛАНДЫ",    "🇳🇱"),
    ("-de-",        "ГЕРМАНИЯ",      "🇩🇪"),
    ("-ru-",        "РОССИЯ",        "🇷🇺"),
    ("-pl-",        "ПОЛЬША",        "🇵🇱"),
    ("-fr-",        "ФРАНЦИЯ",       "🇫🇷"),
]

# Флаги для get_country_flag (обратная совместимость)
_COUNTRY_FLAG_LEGACY: Dict[str, str] = {
    key: flag
    for key, _country, flag in COUNTRY_MAP
}


# ── Декодирование base64 ───────────────────────────────────────────────────────

def _try_decode_base64(value: str) -> Optional[str]:
    """Попытаться декодировать как base64 (требует :// в результате)."""
    compact = re.sub(r"\s+", "", value)
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


def _decode_vmess_payload(payload: str) -> Optional[dict]:
    """Декодировать base64 vmess payload → dict (без требования ://)."""
    compact = re.sub(r"\s+", "", payload)
    if not compact:
        return None
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            padded = compact + "=" * (-len(compact) % 4)
            decoded = decoder(padded.encode("utf-8")).decode("utf-8", errors="ignore")
            obj = json.loads(decoded)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


# ── Извлечение данных из конфигов ─────────────────────────────────────────────

def _looks_like_link(line: str) -> bool:
    return line.startswith(SUPPORTED_SCHEMES)


def _get_fragment(config: str) -> str:
    """Извлечь фрагмент (#...) без URL-decode."""
    if "#" in config:
        return unquote(config.split("#", 1)[1])
    return ""


def _get_vmess_ps(config: str) -> str:
    """Извлечь ps-поле из vmess://."""
    payload = config[len("vmess://"):].strip()
    obj = _decode_vmess_payload(payload)
    if obj:
        return str(obj.get("ps") or "")
    return ""


def _get_vmess_add(config: str) -> str:
    """Извлечь хост (add) из vmess://."""
    payload = config[len("vmess://"):].strip()
    obj = _decode_vmess_payload(payload)
    if obj:
        return str(obj.get("add") or obj.get("host") or obj.get("sni") or "")
    return ""


def _get_config_remark(config: str) -> str:
    """Получить текущее display name конфига."""
    if config.startswith("vmess://"):
        return _get_vmess_ps(config)
    return _get_fragment(config)


def _get_config_host(config: str) -> str:
    """Получить хост/адрес из конфига для геодетекции."""
    if config.startswith("vmess://"):
        return _get_vmess_add(config)
    try:
        from urllib.parse import urlsplit
        base = config.split("#")[0]
        parsed = urlsplit(base)
        return parsed.hostname or ""
    except Exception:
        return ""


# ── Гео-детекция ──────────────────────────────────────────────────────────────

def _detect_country(text: str) -> Tuple[str, str]:
    """Определить страну и флаг по тексту. Возвращает (country_upper, flag)."""
    if not text:
        return "НЕИЗВЕСТНО", "🌐"
    t = text.lower()
    for key, country, flag in COUNTRY_MAP:
        if key in t:
            return country, flag
    return "НЕИЗВЕСТНО", "🌐"


def _detect_protocol(config: str) -> str:
    proto_map = {
        "vmess://":  "VMESS",
        "vless://":  "VLESS",
        "trojan://": "TROJAN",
        "ss://":     "SS",
        "ssr://":    "SSR",
        "hy2://":    "HY2",
        "tuic://":   "TUIC",
    }
    for scheme, proto in proto_map.items():
        if config.startswith(scheme):
            return proto
    return "VPN"


def _is_bypass(text: str) -> bool:
    """Является ли текст маркером обхода глушилок."""
    t = text.lower()
    return any(kw in t for kw in BYPASS_KEYWORDS)


# ── Дедупликация ──────────────────────────────────────────────────────────────

def _config_fingerprint(config: str) -> str:
    """
    Уникальный «смысловой» отпечаток конфига для дедупликации.
    Игнорирует display name (ps/fragment).
    """
    if config.startswith("vmess://"):
        payload = config[len("vmess://"):].strip()
        obj = _decode_vmess_payload(payload)
        if obj:
            obj.pop("ps", None)
            return json.dumps(obj, sort_keys=True, ensure_ascii=False)
        return config
    if "#" in config:
        return config.rsplit("#", 1)[0]
    return config


# ── Запись имени ──────────────────────────────────────────────────────────────

def _set_config_name(config: str, name: str) -> str:
    """Установить display name в конфиг."""
    if config.startswith("vmess://"):
        payload = config[len("vmess://"):].strip()
        obj = _decode_vmess_payload(payload)
        if obj:
            obj["ps"] = name
            encoded = base64.b64encode(
                json.dumps(obj, ensure_ascii=False).encode("utf-8")
            ).decode("utf-8")
            return f"vmess://{encoded}"
    base = config.rsplit("#", 1)[0] if "#" in config else config
    return f"{base}#{quote(name, safe='')}"


# ── Основная функция переименования ──────────────────────────────────────────

def rename_configs(
    configs: List[str],
    *,
    show_first_number: bool = SHOW_FIRST_NUMBER,
) -> List[str]:
    """
    Дедуплицирует и переименовывает конфиги по правилам ТЗ.

    Приоритет источника страны: remark/ps → host → полный конфиг
    """
    # 1) Дедупликация с сохранением порядка
    seen_fp: set = set()
    deduped: List[str] = []
    for cfg in configs:
        fp = _config_fingerprint(cfg)
        if fp not in seen_fp:
            seen_fp.add(fp)
            deduped.append(cfg)

    # 2) Переименование
    group_counters: Dict[str, int] = {}
    bypass_counter = 0
    result: List[str] = []

    for cfg in deduped:
        remark = _get_config_remark(cfg)

        # Проверяем обход по remark и по хосту
        check_texts = [remark, _get_config_host(cfg), cfg]
        is_bp = any(_is_bypass(t) for t in check_texts if t)

        if is_bp:
            bypass_counter += 1
            name = TEMPLATE_BYPASS.format(n=bypass_counter)
        else:
            proto = _detect_protocol(cfg)

            # Приоритет: remark → хост → полный URL конфига
            country, flag = _detect_country(remark)
            if country == "НЕИЗВЕСТНО":
                host = _get_config_host(cfg)
                country, flag = _detect_country(host)
            if country == "НЕИЗВЕСТНО":
                country, flag = _detect_country(cfg)

            key = f"{country}|{proto}"
            group_counters[key] = group_counters.get(key, 0) + 1
            n = group_counters[key]
            if n == 1 and not show_first_number:
                name = TEMPLATE_NORMAL.format(country=country, proto=proto, flag=flag)
            else:
                name = TEMPLATE_NUMBERED.format(country=country, proto=proto, flag=flag, n=n)

        result.append(_set_config_name(cfg, name))

    return result


# ── Парсинг/сборка подписки ───────────────────────────────────────────────────

def parse_subscription_payload(raw_text: str) -> Tuple[List[str], bool]:
    """
    Распарсить тело подписки (plain text или base64).
    Возвращает (список конфигов, was_base64).
    """
    text = raw_text.strip()
    if not text:
        return [], False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    links = [line for line in lines if _looks_like_link(line)]
    if links:
        return links, False
    decoded = _try_decode_base64(text)
    if not decoded:
        return [], False
    decoded_lines = [line.strip() for line in decoded.splitlines() if line.strip()]
    decoded_links = [line for line in decoded_lines if _looks_like_link(line)]
    return decoded_links, True


def build_subscription_payload(configs: List[str], *, as_base64: bool) -> str:
    combined = "\n".join(configs)
    if not as_base64:
        return combined
    return base64.b64encode(combined.encode("utf-8")).decode("utf-8")


# ── Обратная совместимость ────────────────────────────────────────────────────

def get_country_flag(server_name: Optional[str]) -> str:
    """Возвращает флаг страны (обратная совместимость с sub_combiner Flask)."""
    if not server_name:
        return "🌐"
    _, flag = _detect_country(server_name)
    return flag


def modify_config_name(
    config: str,
    *,
    server_name: Optional[str] = None,
    brand_name: str = "CRYSTAL VPN",
    index: Optional[int] = None,
) -> str:
    """Обратно-совместимый хелпер (используется в sub_combiner.py Flask)."""
    if server_name:
        _, flag = _detect_country(server_name)
        title = f"{flag} {server_name}"
    elif index is not None:
        title = f"{brand_name} | обход {index}"
    else:
        title = brand_name
    return _set_config_name(config, title)
