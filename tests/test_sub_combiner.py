"""
Тесты для bot/utils/sub_combiner.py

Проверяют:
  - определение страны и протокола
  - детекцию bypass-узлов
  - переименование (форматы имён)
  - нумерацию групп (COUNTRY+PROTO)
  - дедупликацию по смысловому отпечатку
  - корректную работу с vmess (поле ps)
  - корректную работу с vless/trojan/ss (fragment)
  - full integration test на 12-конфигном наборе
"""

import base64
import json
import sys
import os

# Обеспечиваем импорт из корня проекта
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot.utils.sub_combiner import (
    _detect_country,
    _detect_protocol,
    _is_bypass,
    _config_fingerprint,
    _set_config_name,
    parse_subscription_payload,
    build_subscription_payload,
    rename_configs,
    get_country_flag,
)
from urllib.parse import unquote


# ── Фабрики конфигов ─────────────────────────────────────────────────────────

def make_vless(host: str, name: str = "node") -> str:
    return f"vless://uuid-1234@{host}:443?type=tcp&security=tls#{name}"


def make_vmess(host: str, name: str = "node") -> str:
    obj = {
        "v": "2", "ps": name, "add": host,
        "port": "443", "id": "uuid-1234", "net": "tcp",
        "type": "none", "tls": "tls",
    }
    enc = base64.b64encode(json.dumps(obj).encode()).decode()
    return f"vmess://{enc}"


def make_trojan(host: str, name: str = "node") -> str:
    return f"trojan://password@{host}:443?security=tls#{name}"


def make_ss(host: str, name: str = "node") -> str:
    creds = base64.b64encode(b"aes-256-gcm:password").decode()
    return f"ss://{creds}@{host}:8388#{name}"


def make_hy2(host: str, name: str = "node") -> str:
    return f"hy2://password@{host}:443#{name}"


def get_display_name(cfg: str) -> str:
    """Извлечь display name из конфига."""
    if cfg.startswith("vmess://"):
        obj = json.loads(base64.b64decode(cfg[8:] + "==").decode(errors="ignore"))
        return obj.get("ps", "")
    if "#" in cfg:
        return unquote(cfg.split("#", 1)[1])
    return ""


# ── Тесты _detect_country ────────────────────────────────────────────────────

def test_detect_germany_by_city():
    c, f = _detect_country("frankfurt.example.com")
    assert c == "ГЕРМАНИЯ" and f == "🇩🇪", f"Got: {c}, {f}"


def test_detect_germany_english():
    c, f = _detect_country("germany-server-1")
    assert c == "ГЕРМАНИЯ"


def test_detect_netherlands_by_city():
    c, f = _detect_country("amsterdam-node.example.com")
    assert c == "НИДЕРЛАНДЫ" and f == "🇳🇱"


def test_detect_netherlands_word():
    c, _ = _detect_country("Netherlands proxy")
    assert c == "НИДЕРЛАНДЫ"


def test_detect_poland_cyrillic():
    c, f = _detect_country("польша узел 1")
    assert c == "ПОЛЬША" and f == "🇵🇱"


def test_detect_poland_city():
    c, _ = _detect_country("warsaw.example.com")
    assert c == "ПОЛЬША"


def test_detect_russia_cyrillic():
    c, _ = _detect_country("москва сервер")
    assert c == "РОССИЯ"


def test_detect_russia_english():
    c, _ = _detect_country("russia-node-1")
    assert c == "РОССИЯ"


def test_detect_unknown():
    c, f = _detect_country("xyz-node-unknown")
    assert c == "НЕИЗВЕСТНО" and f == "🌐"


def test_detect_empty():
    c, f = _detect_country("")
    assert c == "НЕИЗВЕСТНО" and f == "🌐"


# ── Тесты _detect_protocol ───────────────────────────────────────────────────

def test_protocol_vless():
    assert _detect_protocol(make_vless("host")) == "VLESS"


def test_protocol_vmess():
    assert _detect_protocol(make_vmess("host")) == "VMESS"


def test_protocol_trojan():
    assert _detect_protocol(make_trojan("host")) == "TROJAN"


def test_protocol_ss():
    assert _detect_protocol(make_ss("host")) == "SS"


def test_protocol_hy2():
    assert _detect_protocol(make_hy2("host")) == "HY2"


# ── Тесты _is_bypass ─────────────────────────────────────────────────────────

def test_bypass_english():
    assert _is_bypass("bypass-server-1")


def test_bypass_anti_block():
    assert _is_bypass("anti-block proxy")


def test_bypass_russian():
    assert _is_bypass("обход глушилок")


def test_bypass_glushlok():
    assert _is_bypass("глушилок-1")


def test_bypass_unblock():
    assert _is_bypass("unblock-russia")


def test_bypass_negative():
    assert not _is_bypass("ГЕРМАНИЯ VLESS")
    assert not _is_bypass("frankfurt.example.com")
    assert not _is_bypass("amsterdam-node")


# ── Тесты дедупликации ───────────────────────────────────────────────────────

def test_fingerprint_ignores_fragment():
    cfg_a = make_vless("host.example.com", "name-A")
    cfg_b = make_vless("host.example.com", "name-B")
    assert _config_fingerprint(cfg_a) == _config_fingerprint(cfg_b)


def test_fingerprint_ignores_vmess_ps():
    cfg_a = make_vmess("host.example.com", "name-A")
    cfg_b = make_vmess("host.example.com", "name-B")
    assert _config_fingerprint(cfg_a) == _config_fingerprint(cfg_b)


def test_fingerprint_different_hosts():
    cfg_a = make_vless("host-a.example.com", "name")
    cfg_b = make_vless("host-b.example.com", "name")
    assert _config_fingerprint(cfg_a) != _config_fingerprint(cfg_b)


def test_dedup_removes_same_host():
    cfg1 = make_vless("frankfurt.example.com", "a")
    cfg2 = make_vless("frankfurt.example.com", "b")
    result = rename_configs([cfg1, cfg2])
    assert len(result) == 1


def test_dedup_keeps_different_hosts():
    cfg1 = make_vless("frankfurt.example.com", "de")
    cfg2 = make_vless("amsterdam.example.com", "nl")
    result = rename_configs([cfg1, cfg2])
    assert len(result) == 2


def test_dedup_preserves_order():
    configs = [
        make_vless("host-c.example.com", "c"),
        make_vless("host-a.example.com", "a"),
        make_vless("host-b.example.com", "b"),
        make_vless("host-a.example.com", "a-dup"),  # duplicate
    ]
    result = rename_configs(configs)
    assert len(result) == 3
    # host-c should still be first
    assert "host-c.example.com" in result[0]


# ── Тесты _set_config_name / encoding ────────────────────────────────────────

def test_set_name_vless_unicode():
    cfg = make_vless("host.example.com", "old")
    new_cfg = _set_config_name(cfg, "ГЕРМАНИЯ VLESS 🇩🇪")
    name = get_display_name(new_cfg)
    assert name == "ГЕРМАНИЯ VLESS 🇩🇪"


def test_set_name_vmess_ps():
    cfg = make_vmess("host.example.com", "old")
    new_cfg = _set_config_name(cfg, "НИДЕРЛАНДЫ VMESS 🇳🇱")
    assert new_cfg.startswith("vmess://")
    obj = json.loads(base64.b64decode(new_cfg[8:] + "==").decode(errors="ignore"))
    assert obj["ps"] == "НИДЕРЛАНДЫ VMESS 🇳🇱"


def test_set_name_preserves_host_vmess():
    """Установка имени не должна менять add-хост."""
    original = make_vmess("amsterdam.example.com", "old")
    new_cfg = _set_config_name(original, "НИДЕРЛАНДЫ VMESS 🇳🇱")
    obj = json.loads(base64.b64decode(new_cfg[8:] + "==").decode(errors="ignore"))
    assert obj["add"] == "amsterdam.example.com"


# ── Тесты rename_configs — форматы имён ──────────────────────────────────────

def test_single_vless_no_number():
    """Первый узел страны не должен получать №1."""
    cfg = make_vless("frankfurt.example.com", "old")
    result = rename_configs([cfg])
    name = get_display_name(result[0])
    assert name == "ГЕРМАНИЯ VLESS 🇩🇪"
    assert "№" not in name


def test_two_germany_vless_numbered():
    """Второй узел той же группы получает №2."""
    cfg1 = make_vless("frankfurt.example.com", "de-1")
    cfg2 = make_vless("berlin.example.com",    "de-2")
    result = rename_configs([cfg1, cfg2])
    names = [get_display_name(r) for r in result]
    assert names[0] == "ГЕРМАНИЯ VLESS 🇩🇪"
    assert names[1] == "ГЕРМАНИЯ VLESS №2 🇩🇪"


def test_three_germany_vless_numbered():
    """Третий узел получает №3."""
    cfg1 = make_vless("frankfurt.example.com", "de-1")
    cfg2 = make_vless("berlin.example.com",    "de-2")
    cfg3 = make_vless("munich.germany.com",    "de-3")
    result = rename_configs([cfg1, cfg2, cfg3])
    names = [get_display_name(r) for r in result]
    assert names[0] == "ГЕРМАНИЯ VLESS 🇩🇪"
    assert names[1] == "ГЕРМАНИЯ VLESS №2 🇩🇪"
    assert names[2] == "ГЕРМАНИЯ VLESS №3 🇩🇪"


def test_bypass_format():
    """Bypass-узел получает формат 'ОБХОД ГЛУШИЛОК №N 🇷🇺'."""
    cfg = make_vless("bypass-server.com", "bypass")
    result = rename_configs([cfg])
    name = get_display_name(result[0])
    assert name == "ОБХОД ГЛУШИЛОК №1 🇷🇺"


def test_two_bypass_numbered():
    cfg1 = make_vless("bypass-1.com", "bypass")
    cfg2 = make_vless("anti-block.com", "antiblock")
    result = rename_configs([cfg1, cfg2])
    names = [get_display_name(r) for r in result]
    assert names[0] == "ОБХОД ГЛУШИЛОК №1 🇷🇺"
    assert names[1] == "ОБХОД ГЛУШИЛОК №2 🇷🇺"


def test_show_first_number_flag():
    """Флаг show_first_number=True добавляет №1."""
    cfg = make_vless("frankfurt.example.com", "de")
    result = rename_configs([cfg], show_first_number=True)
    name = get_display_name(result[0])
    assert "№1" in name


def test_different_protos_same_country_numbered_separately():
    """VLESS и VMESS из Германии — разные счётчики."""
    cfg_vless = make_vless("frankfurt.example.com", "de-vless")
    cfg_vmess = make_vmess("berlin.example.com",    "de-vmess")
    result = rename_configs([cfg_vless, cfg_vmess])
    names = [get_display_name(r) for r in result]
    assert "ГЕРМАНИЯ VLESS 🇩🇪" in names
    assert "ГЕРМАНИЯ VMESS 🇩🇪" in names
    # Ни один не должен получить №2, так как в каждой группе один узел
    for name in names:
        assert "№" not in name


def test_vmess_renamed_ps_field():
    """vmess конфиг: поле ps обновляется, add не трогается."""
    cfg = make_vmess("amsterdam.example.com", "nl-old")
    result = rename_configs([cfg])
    assert result[0].startswith("vmess://")
    obj = json.loads(base64.b64decode(result[0][8:] + "==").decode(errors="ignore"))
    assert "НИДЕРЛАНДЫ" in obj["ps"]
    assert "🇳🇱" in obj["ps"]
    assert obj["add"] == "amsterdam.example.com"


def test_mixed_countries():
    """Проверка правильных имён для трёх разных стран."""
    configs = [
        make_vless("frankfurt.example.com", "de"),
        make_vless("amsterdam.example.com", "nl"),
        make_vless("warsaw.example.com",    "pl"),
    ]
    result = rename_configs(configs)
    names = set(get_display_name(r) for r in result)
    assert "ГЕРМАНИЯ VLESS 🇩🇪" in names
    assert "НИДЕРЛАНДЫ VLESS 🇳🇱" in names
    assert "ПОЛЬША VLESS 🇵🇱" in names


# ── Интеграционный тест (12 конфигов) ────────────────────────────────────────

def test_large_mixed_set():
    """
    Входной набор: 12 конфигов (с дублём).
    Ожидаемый результат: 11 уникальных с правильными именами и нумерацией.
    """
    configs = [
        make_vless("frankfurt.example.com", "de-1"),         # DE VLESS
        make_vless("berlin.example.com",    "de-2"),         # DE VLESS №2
        make_vmess("amsterdam.example.com", "nl-vmess"),     # NL VMESS
        make_trojan("warsaw.example.com",   "pl-trojan"),    # PL TROJAN
        make_vless("bypass-server.com",     "bypass"),       # ОБХОД №1
        make_vless("anti-block.com",        "antiblock"),    # ОБХОД №2
        make_vless("frankfurt.example.com", "de-dup"),       # ДУБЛЬ de-1 (должен удалиться)
        make_vless("paris.example.com",     "fr-1"),         # FR VLESS
        make_vmess("amsterdam2.example.com","nl-vmess-2"),   # NL VMESS №2
        make_vless("bypass3.com",           "обход"),        # ОБХОД №3
        make_vless("london.example.com",    "uk-1"),         # UK VLESS
        make_vless("frankfurt3.example.com","de-3"),         # DE VLESS №3
    ]

    result = rename_configs(configs)
    names = [get_display_name(r) for r in result]

    # Дублирующийся конфиг убран
    assert len(result) == 11, f"Expected 11, got {len(result)}: {names}"

    # Германия — 3 узла с правильной нумерацией
    de_names = [n for n in names if "ГЕРМАНИЯ" in n]
    assert len(de_names) == 3, f"DE: {de_names}"
    assert "ГЕРМАНИЯ VLESS 🇩🇪"    in de_names
    assert "ГЕРМАНИЯ VLESS №2 🇩🇪" in de_names
    assert "ГЕРМАНИЯ VLESS №3 🇩🇪" in de_names

    # Нидерланды — 2 vmess-узла
    nl_names = [n for n in names if "НИДЕРЛАНДЫ" in n]
    assert len(nl_names) == 2, f"NL: {nl_names}"
    assert "НИДЕРЛАНДЫ VMESS 🇳🇱"    in nl_names
    assert "НИДЕРЛАНДЫ VMESS №2 🇳🇱" in nl_names

    # Польша — 1 trojan
    pl_names = [n for n in names if "ПОЛЬША" in n]
    assert len(pl_names) == 1
    assert "ПОЛЬША TROJAN 🇵🇱" in pl_names

    # Франция — 1 vless
    fr_names = [n for n in names if "ФРАНЦИЯ" in n]
    assert len(fr_names) == 1
    assert "ФРАНЦИЯ VLESS 🇫🇷" in fr_names

    # Великобритания — 1 vless
    gb_names = [n for n in names if "ВЕЛИКОБРИТАНИЯ" in n]
    assert len(gb_names) == 1

    # Обход — 3 узла
    bypass_names = [n for n in names if "ОБХОД" in n]
    assert len(bypass_names) == 3, f"Bypass: {bypass_names}"
    assert "ОБХОД ГЛУШИЛОК №1 🇷🇺" in bypass_names
    assert "ОБХОД ГЛУШИЛОК №2 🇷🇺" in bypass_names
    assert "ОБХОД ГЛУШИЛОК №3 🇷🇺" in bypass_names


# ── Тесты parse_subscription_payload ────────────────────────────────────────

def test_parse_plain_text():
    raw = (
        "vless://uuid@host-a:1#A\n"
        "trojan://pass@host-b:2#B\n"
        "vmess://abc\n"
    )
    links, was_b64 = parse_subscription_payload(raw)
    # vmess://abc не является валидным конфигом, но starts with vmess://
    assert "vless://uuid@host-a:1#A" in links
    assert "trojan://pass@host-b:2#B" in links
    assert not was_b64


def test_parse_base64():
    raw = "vless://a@b:1#test\ntrojan://x@y:2#t"
    encoded = base64.b64encode(raw.encode()).decode()
    links, was_b64 = parse_subscription_payload(encoded)
    assert len(links) == 2
    assert was_b64


def test_parse_empty():
    links, was_b64 = parse_subscription_payload("")
    assert links == []
    assert not was_b64


def test_build_base64_roundtrip():
    configs = ["vless://a@b:1#test", "trojan://x@y:2#t"]
    encoded = build_subscription_payload(configs, as_base64=True)
    decoded = base64.b64decode(encoded).decode()
    for c in configs:
        assert c in decoded


def test_build_plain():
    configs = ["vless://a@b:1#test", "trojan://x@y:2#t"]
    result = build_subscription_payload(configs, as_base64=False)
    assert result == "vless://a@b:1#test\ntrojan://x@y:2#t"


# ── Тесты get_country_flag (обратная совместимость) ──────────────────────────

def test_get_country_flag_germany():
    assert get_country_flag("germany-server") == "🇩🇪"


def test_get_country_flag_none():
    assert get_country_flag(None) == "🌐"


def test_get_country_flag_unknown():
    assert get_country_flag("unknown-xyz") == "🌐"


# ── Запуск ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback

    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✓  {fn.__name__}")
            passed += 1
        except Exception:
            print(f"  ✗  {fn.__name__}")
            traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed, {failed} failed out of {passed + failed} tests")
    sys.exit(0 if failed == 0 else 1)
