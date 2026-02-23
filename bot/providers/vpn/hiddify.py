import re
import ssl
import time
import logging
import base64
import json
import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse, urlsplit, urlunsplit

import aiohttp

from bot.config import settings
from bot.providers.vpn.base import VpnKeyData, VpnKeyProvider
from bot.utils.sub_combiner import build_subscription_payload, modify_config_name, parse_subscription_payload

logger = logging.getLogger(__name__)


class HiddifyVpnKeyProvider(VpnKeyProvider):
    _TOTAL_DEADLINE_SECONDS = 18
    _REQUEST_TIMEOUT_SECONDS = 5

    async def issue_key(
        self,
        user_id: int,
        plan_slug: str,
        traffic_gb: int | None = None,
        duration_days: int | None = None,
        build_preset: str | None = None,
    ) -> VpnKeyData:
        api_base, api_key = self._resolve_api_access()
        payload_variants = self._build_payload_variants(
            user_id=user_id,
            plan_slug=plan_slug,
            traffic_gb=traffic_gb,
            duration_days=duration_days,
            build_preset=build_preset,
        )
        connector = self._build_connector()

        async with aiohttp.ClientSession(connector=connector) as session:
            result: Any | None = None
            last_error: str = "unknown error"
            attempts = self._build_attempts(api_base, api_key)
            errors: list[str] = []
            deadline = time.monotonic() + self._TOTAL_DEADLINE_SECONDS
            for idx, payload in enumerate(payload_variants, start=1):
                for method, endpoint, headers, params in attempts:
                    if time.monotonic() >= deadline:
                        break
                    try:
                        response = await session.request(
                            method,
                            endpoint,
                            json=payload,
                            headers=headers,
                            params=params,
                            timeout=self._REQUEST_TIMEOUT_SECONDS,
                        )
                        body = await response.text()
                        if response.status < 400:
                            result = await self._safe_json(response, body)
                            break
                        err = (
                            f"[payload#{idx}] {method} {endpoint} -> HTTP {response.status}: "
                            f"{self._compact_error(body)}"
                        )
                        last_error = err
                        errors.append(err)
                    except aiohttp.ClientError as exc:
                        err = f"[payload#{idx}] {method} {endpoint} -> {exc}"
                        last_error = err
                        errors.append(err)
                if result is not None:
                    break
                if time.monotonic() >= deadline:
                    break

            if result is None:
                details = " | ".join(errors[:3]) if errors else last_error
                raise ValueError(f"Hiddify user create failed: {details}")

            key = self._extract_key(result)
            if not key:
                fetched = await self._fetch_created_user_payload(
                    session=session,
                    api_base=api_base,
                    api_key=api_key,
                    create_result=result,
                    user_id=user_id,
                    deadline=deadline,
                )
                if fetched is not None:
                    key = self._extract_key(fetched)
                    if not key:
                        refs = self._collect_user_refs(fetched)
                        key = self._build_key_from_template(self._pick_uuid(refs.get("ids", [])))
            if not key:
                refs = self._collect_user_refs(result)
                key = self._build_key_from_template(self._pick_uuid(refs.get("ids", [])))
            if not key:
                raise ValueError("Hiddify user created, but key/subscription URL not found in response.")
            if key.startswith(("http://", "https://")):
                combiner_key = self._build_combiner_subscription_url(key)
                if combiner_key:
                    return VpnKeyData(key=combiner_key, meta={"provider": "hiddify", "raw": result})
            # Prefer "cleaned + rebuilt" subscription from provider payload.
            # Rebuild is independent from create-user deadline to avoid returning raw URL.
            rebuild_deadline = time.monotonic() + 30
            rebuilt = await self._try_rebuild_subscription_payload(session, key, rebuild_deadline)
            if rebuilt:
                # Keep URL for bot UX. Rebuilt non-URL payloads are not suitable for "tap to open".
                if rebuilt.startswith(("http://", "https://")):
                    key = rebuilt
            else:
                key = self._append_profile_name(key, user_id)
            return VpnKeyData(key=key, meta={"provider": "hiddify", "raw": result})

    async def revoke_key(self, key: str) -> None:
        return None

    def _resolve_api_access(self) -> tuple[str, str]:
        base = (settings.vpn_api_base_url or "").strip().rstrip("/")
        if not base:
            raise ValueError("VPN_API_BASE_URL is not configured.")
        parsed = urlparse(base)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("VPN_API_BASE_URL is invalid.")
        api_key = (settings.vpn_api_key or settings.hiddify_api_key or "").strip()
        if not api_key:
            raise ValueError("VPN_API_KEY is missing.")
        return base, api_key

    def _build_payload_variants(
        self,
        user_id: int,
        plan_slug: str,
        traffic_gb: int | None,
        duration_days: int | None,
        build_preset: str | None = None,
    ) -> list[Any]:
        profile_name = settings.hiddify_profile_name or "CRYSTAL VPN"
        traffic = traffic_gb or settings.hiddify_default_traffic_gb
        days = duration_days or settings.hiddify_default_duration_days
        now_iso = datetime.now(timezone.utc).isoformat()

        username = f"user_{user_id}"
        base = {"name": f"{profile_name}"}
        core_variants: list[dict[str, Any]] = [
            {
                **base,
                "usage_limit_GB": int(traffic),
                "package_days": int(days),
            },
            {
                **base,
                "usage_limit_gb": int(traffic),
                "package_days": int(days),
            },
            {
                **base,
                "usage_limit_GB": int(traffic),
                "days": int(days),
            },
            {
                **base,
                "traffic": int(traffic),
                "package_days": int(days),
            },
            {
                **base,
                "usage_limit_GB": int(traffic),
                "package_days": int(days),
                "username": username,
            },
            {
                **base,
                "usage_limit_GB": int(traffic),
                "package_days": int(days),
                "comment": f"tg:{user_id}; plan:{plan_slug}",
            },
            {
                **base,
                "usage_limit_GB": int(traffic),
                "package_days": int(days),
                "mode": "no_reset",
            },
            {
                **base,
                "usage_limit_GB": int(traffic),
                "package_days": int(days),
                "start_date": now_iso,
            },
        ]
        variants: list[Any] = []
        inbound_names = [f"обход {idx}" for idx in range(1, 9)]
        preset = (build_preset or "max").strip().lower()
        full_flags: dict[str, Any] = {
            "build_preset": preset,
            "preset": preset,
            "profile_preset": preset,
            "config_mode": "full" if preset == "max" else preset,
        }
        for item in core_variants:
            # Keep known-good payloads first for maximum compatibility and speed.
            variants.append(item)
            variants.append({"user": item})
            variants.append({"users": [item]})
            variants.append([item])  # type: ignore[list-item]

            # Optional enrichment attempts: build preset and custom inbound names.
            enriched_item = {**item, **full_flags}
            variants.append(enriched_item)
            variants.append({"user": enriched_item})
            variants.append({"users": [enriched_item]})
            variants.append([enriched_item])  # type: ignore[list-item]

            for enriched in self._build_inbounds_payload_variants(item, inbound_names):
                variants.append(enriched)
                variants.append({"user": enriched})
                variants.append({"users": [enriched]})
                variants.append([enriched])  # type: ignore[list-item]
        # Remove None values defensively.
        clean_variants: list[Any] = []
        for item in variants:
            if isinstance(item, dict):
                clean_variants.append({k: v for k, v in item.items() if v is not None})
            else:
                clean_variants.append(item)
        return clean_variants

    def _build_inbounds_payload_variants(self, base: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
        return [
            {**base, "inbounds": [{"name": name} for name in names]},
            {**base, "inbounds": [{"tag": name, "name": name} for name in names]},
            {**base, "inbounds": names},
            {**base, "inbound_names": names},
            {**base, "proxy_names": names},
            {**base, "config_names": names},
        ]

    def _build_attempts(
        self,
        api_base: str,
        api_key: str,
    ) -> list[tuple[str, str, dict[str, str], dict[str, str]]]:
        clean_base = api_base.rstrip("/")
        path_bases = [clean_base] if clean_base.endswith("/api/v2/admin") else [f"{clean_base}/api/v2/admin"]
        auth_headers = (
            {"Hiddify-API-Key": api_key},
            {"Authorization": f"Bearer {api_key}"},
        )
        query_params = ({},)
        endpoints = ("user", "users")
        attempts: list[tuple[str, str, dict[str, str], dict[str, str]]] = []
        for base in path_bases:
            for endpoint in endpoints:
                for headers in auth_headers:
                    for params in query_params:
                        for method in ("POST",):
                            for trailing in (True, False):
                                url = f"{base}/{endpoint}/" if trailing else f"{base}/{endpoint}"
                                attempts.append((method, url, headers, params))
        return attempts

    async def _safe_json(self, response: aiohttp.ClientResponse, body: str) -> Any:
        ctype = (response.headers.get("Content-Type") or "").lower()
        if "application/json" in ctype:
            return await response.json()
        return {"text": body}

    async def _fetch_created_user_payload(
        self,
        session: aiohttp.ClientSession,
        api_base: str,
        api_key: str,
        create_result: Any,
        user_id: int,
        deadline: float,
    ) -> Any | None:
        refs = self._collect_user_refs(create_result)
        if not refs["ids"]:
            refs["ids"].append(str(user_id))
        refs["usernames"].append(f"user_{user_id}")
        refs["names"].append(f"{settings.hiddify_profile_name or 'CRYSTAL VPN'} {user_id}")

        for method, endpoint, headers, params in self._build_fetch_attempts(api_base, api_key, refs):
            if time.monotonic() >= deadline:
                return None
            try:
                response = await session.request(
                    method,
                    endpoint,
                    headers=headers,
                    params=params,
                    timeout=self._REQUEST_TIMEOUT_SECONDS,
                )
                body = await response.text()
                if response.status >= 400:
                    continue
                payload = await self._safe_json(response, body)
                key = self._extract_key(payload)
                if key:
                    return payload
                matched = self._find_user_in_payload(payload, refs, user_id)
                if matched is not None:
                    return matched
            except aiohttp.ClientError:
                continue
        return None

    async def _try_rename_user_inbounds(
        self,
        session: aiohttp.ClientSession,
        api_base: str,
        api_key: str,
        create_result: Any,
        user_id: int,
        deadline: float,
    ) -> None:
        refs = self._collect_user_refs(create_result)
        if not refs["ids"]:
            refs["ids"].append(str(user_id))
        names = [f"обход {idx}" for idx in range(1, 9)]
        base_name = f"{settings.hiddify_profile_name or 'CRYSTAL VPN'}"
        payloads = self._build_inbounds_payload_variants({"name": base_name}, names)
        payloads.extend(
            [
                {"name": base_name, "comment": f"inbounds: {', '.join(names)}"},
                {"name": base_name, "comment": "vpn profile with custom inbound names"},
            ]
        )
        attempts = self._build_update_attempts(api_base, api_key, refs)
        for payload in payloads:
            for method, endpoint, headers, params in attempts:
                if time.monotonic() >= deadline:
                    return
                try:
                    response = await session.request(
                        method,
                        endpoint,
                        headers=headers,
                        params=params,
                        json=payload,
                        timeout=self._REQUEST_TIMEOUT_SECONDS,
                    )
                    if response.status < 400:
                        logger.info(
                            "Hiddify inbound rename request accepted: %s %s payload_keys=%s",
                            method,
                            endpoint,
                            ",".join(payload.keys()),
                        )
                        return
                except aiohttp.ClientError:
                    continue
        logger.warning("Hiddify inbound rename request was not accepted by API.")

    async def _try_rename_global_inbounds(
        self,
        session: aiohttp.ClientSession,
        api_base: str,
        api_key: str,
        deadline: float,
    ) -> None:
        for get_method, get_endpoint, headers, params in self._build_global_fetch_attempts(api_base, api_key):
            if time.monotonic() >= deadline:
                return
            try:
                response = await session.request(
                    get_method,
                    get_endpoint,
                    headers=headers,
                    params=params,
                    timeout=self._REQUEST_TIMEOUT_SECONDS,
                )
                if response.status >= 400:
                    continue
                payload = await self._safe_json(response, await response.text())
            except aiohttp.ClientError:
                continue

            entities = self._extract_named_entities(payload)
            if not entities:
                continue
            renamed = 0
            for idx, entity in enumerate(entities, start=1):
                if time.monotonic() >= deadline:
                    return
                if idx > 12:
                    break
                label = f"обход {idx}"
                ref = entity.get("id") or entity.get("uuid") or entity.get("user_id")
                if ref is None:
                    continue
                ref_text = str(ref).strip()
                if not ref_text:
                    continue
                ok = await self._try_update_entity_name(
                    session=session,
                    attempts=self._build_global_update_attempts(api_base, api_key, ref_text),
                    label=label,
                    deadline=deadline,
                )
                if ok:
                    renamed += 1
            if renamed > 0:
                logger.info("Hiddify global inbound/proxy rename applied: %s entities.", renamed)
                return
        logger.warning("Hiddify global inbound/proxy rename was not accepted by API.")

    async def _try_update_entity_name(
        self,
        session: aiohttp.ClientSession,
        attempts: list[tuple[str, str, dict[str, str], dict[str, str]]],
        label: str,
        deadline: float,
    ) -> bool:
        payloads = [
            {"name": label},
            {"remark": label},
            {"title": label},
            {"tag": label},
            {"label": label},
            {"comment": label},
            {"name": label, "remark": label},
            {"name": label, "tag": label},
        ]
        for payload in payloads:
            for method, endpoint, headers, params in attempts:
                if time.monotonic() >= deadline:
                    return False
                try:
                    response = await session.request(
                        method,
                        endpoint,
                        headers=headers,
                        params=params,
                        json=payload,
                        timeout=self._REQUEST_TIMEOUT_SECONDS,
                    )
                    if response.status < 400:
                        return True
                except aiohttp.ClientError:
                    continue
        return False

    def _collect_user_refs(self, data: Any) -> dict[str, list[str]]:
        ids: list[str] = []
        usernames: list[str] = []
        names: list[str] = []

        def _walk(node: Any) -> None:
            if isinstance(node, dict):
                for field in ("uuid", "user_uuid", "id", "user_id"):
                    value = node.get(field)
                    if value is not None:
                        text = str(value).strip()
                        if text and text not in ids:
                            ids.append(text)
                for field in ("username",):
                    value = node.get(field)
                    if isinstance(value, str):
                        text = value.strip()
                        if text and text not in usernames:
                            usernames.append(text)
                for field in ("name",):
                    value = node.get(field)
                    if isinstance(value, str):
                        text = value.strip()
                        if text and text not in names:
                            names.append(text)
                for value in node.values():
                    _walk(value)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(data)
        return {"ids": ids, "usernames": usernames, "names": names}

    def _build_fetch_attempts(
        self,
        api_base: str,
        api_key: str,
        refs: dict[str, list[str]],
    ) -> list[tuple[str, str, dict[str, str], dict[str, str]]]:
        clean_base = api_base.rstrip("/")
        bases = [clean_base] if clean_base.endswith("/api/v2/admin") else [f"{clean_base}/api/v2/admin"]
        auth_headers = (
            {"Hiddify-API-Key": api_key},
            {"Authorization": f"Bearer {api_key}"},
        )
        attempts: list[tuple[str, str, dict[str, str], dict[str, str]]] = []
        for base in bases:
            for headers in auth_headers:
                attempts.append(("GET", f"{base}/users/", headers, {}))
                attempts.append(("GET", f"{base}/users", headers, {}))
                for ref in refs["ids"]:
                    attempts.append(("GET", f"{base}/user/{ref}/", headers, {}))
                    attempts.append(("GET", f"{base}/user/{ref}", headers, {}))
                    attempts.append(("GET", f"{base}/users/{ref}/", headers, {}))
                    attempts.append(("GET", f"{base}/users/{ref}", headers, {}))
                    attempts.append(("GET", f"{base}/user/", headers, {"uuid": ref}))
                    attempts.append(("GET", f"{base}/users/", headers, {"uuid": ref}))
                for username in refs["usernames"]:
                    attempts.append(("GET", f"{base}/user/", headers, {"username": username}))
                    attempts.append(("GET", f"{base}/users/", headers, {"username": username}))
        return attempts

    def _build_global_fetch_attempts(
        self,
        api_base: str,
        api_key: str,
    ) -> list[tuple[str, str, dict[str, str], dict[str, str]]]:
        clean_base = api_base.rstrip("/")
        bases = [clean_base] if clean_base.endswith("/api/v2/admin") else [f"{clean_base}/api/v2/admin"]
        auth_headers = (
            {"Hiddify-API-Key": api_key},
            {"Authorization": f"Bearer {api_key}"},
        )
        resources = ("inbounds", "inbound", "proxies", "proxy", "nodes", "node")
        attempts: list[tuple[str, str, dict[str, str], dict[str, str]]] = []
        for base in bases:
            for headers in auth_headers:
                for resource in resources:
                    attempts.append(("GET", f"{base}/{resource}/", headers, {}))
                    attempts.append(("GET", f"{base}/{resource}", headers, {}))
        return attempts

    def _build_update_attempts(
        self,
        api_base: str,
        api_key: str,
        refs: dict[str, list[str]],
    ) -> list[tuple[str, str, dict[str, str], dict[str, str]]]:
        clean_base = api_base.rstrip("/")
        bases = [clean_base] if clean_base.endswith("/api/v2/admin") else [f"{clean_base}/api/v2/admin"]
        auth_headers = (
            {"Hiddify-API-Key": api_key},
            {"Authorization": f"Bearer {api_key}"},
        )
        attempts: list[tuple[str, str, dict[str, str], dict[str, str]]] = []
        for base in bases:
            for headers in auth_headers:
                for ref in refs["ids"]:
                    attempts.append(("PATCH", f"{base}/user/{ref}/", headers, {}))
                    attempts.append(("PATCH", f"{base}/user/{ref}", headers, {}))
                    attempts.append(("PUT", f"{base}/user/{ref}/", headers, {}))
                    attempts.append(("PUT", f"{base}/user/{ref}", headers, {}))
        return attempts

    def _build_global_update_attempts(
        self,
        api_base: str,
        api_key: str,
        ref: str,
    ) -> list[tuple[str, str, dict[str, str], dict[str, str]]]:
        clean_base = api_base.rstrip("/")
        bases = [clean_base] if clean_base.endswith("/api/v2/admin") else [f"{clean_base}/api/v2/admin"]
        auth_headers = (
            {"Hiddify-API-Key": api_key},
            {"Authorization": f"Bearer {api_key}"},
        )
        roots = ("inbound", "inbounds", "proxy", "proxies", "node", "nodes")
        attempts: list[tuple[str, str, dict[str, str], dict[str, str]]] = []
        for base in bases:
            for headers in auth_headers:
                for root in roots:
                    for method in ("PATCH", "PUT", "POST"):
                        attempts.append((method, f"{base}/{root}/{ref}/", headers, {}))
                        attempts.append((method, f"{base}/{root}/{ref}", headers, {}))
        return attempts

    def _extract_named_entities(self, payload: Any) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        def _walk(node: Any) -> None:
            if isinstance(node, dict):
                has_ref = any(node.get(field) is not None for field in ("id", "uuid", "user_id"))
                has_name_like = any(
                    isinstance(node.get(field), str)
                    for field in ("name", "remark", "title", "tag", "label")
                )
                if has_ref and has_name_like:
                    result.append(node)
                for value in node.values():
                    _walk(value)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(payload)
        return result

    def _find_user_in_payload(self, payload: Any, refs: dict[str, list[str]], user_id: int) -> Any | None:
        if isinstance(payload, dict):
            key = self._extract_key(payload)
            if key:
                return payload
            for field in ("id", "uuid", "user_id", "user_uuid"):
                value = payload.get(field)
                if value is not None and str(value).strip() in refs["ids"]:
                    return payload
            username = payload.get("username")
            if isinstance(username, str) and username.strip() in refs["usernames"]:
                return payload
            name = payload.get("name")
            if isinstance(name, str):
                n = name.strip()
                if n in refs["names"] or n.endswith(str(user_id)):
                    return payload
            for value in payload.values():
                found = self._find_user_in_payload(value, refs, user_id)
                if found is not None:
                    return found
        elif isinstance(payload, list):
            for item in payload:
                found = self._find_user_in_payload(item, refs, user_id)
                if found is not None:
                    return found
        return None

    def _pick_uuid(self, values: list[str]) -> str | None:
        for value in values:
            v = value.strip()
            if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", v):
                return v
        return None

    def _build_key_from_template(self, uuid_value: str | None) -> str | None:
        if not uuid_value:
            return None
        template = (settings.hiddify_subscription_url_template or "").strip()
        if not template:
            return None
        if "{uuid}" in template:
            return template.replace("{uuid}", uuid_value)
        base = template.rstrip("/")
        return f"{base}/{uuid_value}/"

    def _extract_key(self, data: Any) -> str | None:
        if isinstance(data, dict):
            for field in ("subscription_url", "sub_link", "link", "url", "raw_link", "vless"):
                value = data.get(field)
                if isinstance(value, str) and value.strip():
                    return value.strip()

            # Some Hiddify versions return arrays with links.
            for field in ("links", "subscriptions", "subscription_links", "sub_links"):
                links = data.get(field)
                extracted = self._extract_key(links)
                if extracted:
                    return extracted

            for field in ("data", "user", "result", "obj", "item", "items"):
                nested = data.get(field)
                if nested is None:
                    continue
                extracted = self._extract_key(nested)
                if extracted:
                    return extracted
        if isinstance(data, list):
            for item in data:
                extracted = self._extract_key(item)
                if extracted:
                    return extracted
        if isinstance(data, str):
            text = data.strip()
            if text.startswith(("http://", "https://", "vless://", "vmess://", "trojan://", "ss://", "ssr://")):
                return text
        return None

    def _append_profile_name(self, key: str, user_id: int) -> str:
        profile = (settings.hiddify_profile_name or "CRYSTAL VPN").strip()
        if not profile:
            return key
        profile_slug = re.sub(r"[^A-Za-z0-9]+", "-", profile).strip("-")
        if not profile_slug:
            profile_slug = "CRYSTAL-VPN"
        fragment = profile_slug
        parts = urlsplit(key)
        # Force custom profile title even if provider returned fragment with IP.
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, quote(fragment)))

    def _build_combiner_subscription_url(self, source_url: str) -> str | None:
        base = (settings.sub_combiner_base_url or settings.sub_domain or "").strip().rstrip("/")
        if not base:
            logger.warning(
                "Combiner URL is not configured. Set SUB_COMBINER_BASE_URL or SUB_DOMAIN to enable subscription rewrite."
            )
            return None
        return f"{base}/sub_from_url?src={quote(source_url, safe='')}"

    def _compact_error(self, body: str) -> str:
        text = re.sub(r"<[^>]+>", " ", body)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 240:
            return text[:240] + "..."
        return text

    async def _try_rebuild_subscription_payload(
        self,
        session: aiohttp.ClientSession,
        key: str,
        deadline: float,
    ) -> str | None:
        if not key.startswith(("http://", "https://")):
            rebuilt = self._rebuild_subscription_text(key)
            return rebuilt or None
        parsed = urlsplit(key)
        base_q = parsed.query
        query_variants = [
            base_q,
            "&".join(chunk for chunk in [base_q, "sub=1"] if chunk),
            "&".join(chunk for chunk in [base_q, "clash=1"] if chunk),
            "&".join(chunk for chunk in [base_q, "base64=1"] if chunk),
            "&".join(chunk for chunk in [base_q, "raw=1"] if chunk),
        ]
        user_agents = (
            "hiddify-next",
            "clash-meta",
            "v2rayN",
            "Streisand",
            "CrystalVPNBot/1.0",
        )
        candidates = []
        for q in query_variants:
            candidates.append(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, q, "")))
        for candidate in candidates:
            for ua in user_agents:
                timeout_left = max(1.0, deadline - time.monotonic())
                timeout = min(12.0, timeout_left)
                if timeout_left <= 0:
                    return None
                try:
                    response = await session.get(
                        candidate,
                        timeout=timeout,
                        headers={
                            "Accept": "text/plain,application/json,*/*",
                            "User-Agent": ua,
                        },
                    )
                    body = await response.text()
                    if response.status >= 400:
                        continue
                    # If server returned HTML/error page, skip rewrite.
                    if "<html" in body.lower():
                        continue
                    rebuilt = self._rebuild_subscription_text(body)
                    if rebuilt:
                        return rebuilt
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    continue
        logger.warning("Could not fetch parseable subscription body from source URL, fallback to raw URL.")
        return None

    def _rebuild_subscription_text(self, raw: str) -> str | None:
        text = raw.strip()
        if not text:
            return None
        links, source_is_base64 = parse_subscription_payload(text)
        if not links:
            return None
        brand_name = (settings.hiddify_profile_name or "CRYSTAL VPN").strip() or "CRYSTAL VPN"
        renamed = [modify_config_name(link, brand_name=brand_name, index=idx) for idx, link in enumerate(links, start=1)]
        return build_subscription_payload(renamed, as_base64=source_is_base64)

    def _extract_subscription_links(self, text: str) -> tuple[list[str], bool]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        links = [line for line in lines if self._looks_like_link(line)]
        if links:
            return links, False
        decoded = self._try_decode_base64_text(text)
        if not decoded:
            return [], False
        decoded_lines = [line.strip() for line in decoded.splitlines() if line.strip()]
        decoded_links = [line for line in decoded_lines if self._looks_like_link(line)]
        return decoded_links, True

    def _looks_like_link(self, line: str) -> bool:
        return line.startswith(("vmess://", "vless://", "trojan://", "ss://", "ssr://", "hy2://", "tuic://"))

    def _try_decode_base64_text(self, value: str) -> str | None:
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

    def _rename_subscription_link(self, link: str, idx: int) -> str:
        name = f"{(settings.hiddify_profile_name or 'CRYSTAL VPN').strip()} | обход {idx}"
        if link.startswith("vmess://"):
            payload = link[len("vmess://") :].strip()
            decoded = self._try_decode_base64_text(payload)
            if decoded:
                try:
                    obj = json.loads(decoded)
                    if isinstance(obj, dict):
                        obj["ps"] = name
                        encoded = base64.b64encode(json.dumps(obj, ensure_ascii=False).encode("utf-8")).decode("utf-8")
                        return f"vmess://{encoded}"
                except Exception:
                    return link
            return link
        try:
            parts = urlsplit(link)
            return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, quote(name)))
        except Exception:
            if "#" in link:
                return f"{link.split('#', 1)[0]}#{quote(name)}"
            return f"{link}#{quote(name)}"

    def _build_connector(self) -> aiohttp.TCPConnector | None:
        if settings.hiddify_verify_ssl:
            return None
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        return aiohttp.TCPConnector(ssl=ssl_context)
