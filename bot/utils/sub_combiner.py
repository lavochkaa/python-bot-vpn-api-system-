import base64
import json
import re
from typing import Tuple
from urllib.parse import quote


SUPPORTED_SCHEMES = ("vmess://", "vless://", "trojan://", "ss://", "ssr://", "hy2://", "tuic://")


def get_country_flag(server_name: str | None) -> str:
    if not server_name:
        return "🌐"
    name_lower = server_name.lower()
    flags = {
        "russia": "🇷🇺",
        "ru": "🇷🇺",
        "netherlands": "🇳🇱",
        "nl": "🇳🇱",
        "germany": "🇩🇪",
        "de": "🇩🇪",
        "france": "🇫🇷",
        "fr": "🇫🇷",
        "usa": "🇺🇸",
        "united states": "🇺🇸",
        "us": "🇺🇸",
        "uk": "🇬🇧",
        "england": "🇬🇧",
        "canada": "🇨🇦",
        "ca": "🇨🇦",
        "singapore": "🇸🇬",
        "sg": "🇸🇬",
        "japan": "🇯🇵",
        "jp": "🇯🇵",
        "korea": "🇰🇷",
        "kr": "🇰🇷",
        "australia": "🇦🇺",
        "au": "🇦🇺",
        "turkey": "🇹🇷",
        "tr": "🇹🇷",
    }
    for key, flag in flags.items():
        if key in name_lower:
            return flag
    return "🌐"


def _try_decode_base64(value: str) -> str | None:
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


def _looks_like_link(line: str) -> bool:
    return line.startswith(SUPPORTED_SCHEMES)


def modify_config_name(
    config: str,
    *,
    server_name: str | None = None,
    brand_name: str = "CRYSTAL VPN",
    index: int | None = None,
) -> str:
    if server_name:
        title = f"{get_country_flag(server_name)} {server_name}"
    elif index is not None:
        title = f"{brand_name} | обход {index}"
    else:
        title = brand_name

    if config.startswith("vmess://"):
        vmess_payload = config[len("vmess://") :].strip()
        decoded = _try_decode_base64(vmess_payload)
        if decoded:
            try:
                obj = json.loads(decoded)
                if isinstance(obj, dict):
                    obj["ps"] = title
                    encoded = base64.b64encode(
                        json.dumps(obj, ensure_ascii=False).encode("utf-8")
                    ).decode("utf-8")
                    return f"vmess://{encoded}"
            except Exception:
                pass

    if "#" in config:
        base_url, _ = config.rsplit("#", 1)
        return f"{base_url}#{quote(title)}"
    return f"{config}#{quote(title)}"


def parse_subscription_payload(raw_text: str) -> Tuple[list[str], bool]:
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


def build_subscription_payload(configs: list[str], *, as_base64: bool) -> str:
    combined = "\n".join(configs)
    if not as_base64:
        return combined
    return base64.b64encode(combined.encode("utf-8")).decode("utf-8")
