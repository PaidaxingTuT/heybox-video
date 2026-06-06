import hashlib
import html
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from playwright.sync_api import Error as PlaywrightError

from config import (
    CAPTCHA_MANUAL_TIMEOUT,
    PLAYWRIGHT_HEADLESS,
    PLAYWRIGHT_TIMEOUT,
    PLAYWRIGHT_USER_DATA_DIR,
)

LOGGER = logging.getLogger("uvicorn.error")


class HeyboxFastMixin:
    FAST_REQUEST_TIMEOUT = 8
    FAST_SCRIPT_LIMIT = 4
    FAST_STATE_VERSION = 1
    HKEY_ALPHABET = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"
    META_SKIP_KEYS = {
        "comment",
        "comments",
        "reply_comment",
        "reply_comments",
        "reply_list",
        "replyuser",
        "reply_user",
        "user",
        "users",
    }
    PRIMARY_META_CONTAINER_KEYS = ("link", "post", "article", "link_info")
    PRIMARY_CONTAINER_PARENT_KEYS = ("result", "data")
    VIDEO_URL_RE = re.compile(
        r"https?://[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+?(?:\.mp4|\.m3u8)(?:[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]*)",
        re.IGNORECASE,
    )
    LINK_ID_PATH_RE = re.compile(r"/bbs/link/([A-Za-z0-9]+)", re.IGNORECASE)
    LINK_ID_TEXT_RE = re.compile(r"""(?:link_id|linkid)["'=:/\\\s]+([A-Za-z0-9]+)""", re.IGNORECASE)
    SCRIPT_SRC_RE = re.compile(r"""<script[^>]+src=["']([^"']+)["']""", re.IGNORECASE)
    META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
    TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
    VIDEO_CONTENT_TYPES = (
        "video/",
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
    )
    FAST_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }
    TITLE_KEYS = ("title", "share_title", "link_title", "post_title")
    PRIMARY_DESCRIPTION_KEYS = (
        "description",
        "desc",
        "summary",
        "brief",
        "share_desc",
        "content",
        "text",
    )
    FALLBACK_DESCRIPTION_KEYS = (
        "description",
        "desc",
        "summary",
        "brief",
        "share_desc",
    )
    GENERIC_TITLES = {
        "\u5c0f\u9ed1\u76d2",
        "\u9ad8\u80fd\u73a9\u5bb6\u805a\u96c6\u5730 - \u5c0f\u9ed1\u76d2",
        "楂樿兘鐜╁鑱氶泦鍦?- 灏忛粦鐩? ",
        "鐩稿叧绀惧尯",
        "鎺ㄨ崘鍐呭",
        "鐩稿叧鎺ㄨ崘",
    }
    FAST_STATE_STORAGE_KEYS = {
        "heyboxid": "heybox_id",
        "deviceid": "device_id",
        "visitorid": "device_id",
        "visitor_id": "device_id",
        "fingerprintid": "device_id",
        "fingerprintvisitorid": "device_id",
    }

    @classmethod
    def _looks_like_video_url(cls, url: str) -> bool:
        lower_url = url.lower()
        return ".mp4" in lower_url or ".m3u8" in lower_url

    @staticmethod
    def _is_post_detail_response(url: str) -> bool:
        return "/bbs/app/link/tree" in url.lower()

    @classmethod
    def _is_generic_title(cls, value: str | None) -> bool:
        return not value or value in cls.GENERIC_TITLES

    @staticmethod
    def _profile_dir() -> str:
        return str((Path(__file__).resolve().parent / PLAYWRIGHT_USER_DATA_DIR).absolute())

    @classmethod
    def _fast_state_path(cls) -> Path:
        return Path(cls._profile_dir()) / "fast-session.json"

    @staticmethod
    def _clean_text(value: Any, max_length: int = 240) -> str | None:
        if not isinstance(value, str):
            return None

        text = re.sub(r"<[^>]+>", " ", value)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return None

        return text[:max_length]

    @classmethod
    def _clean_meta(cls, meta: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}

        title = cls._clean_text(meta.get("title"), max_length=120)
        if title and not cls._is_generic_title(title):
            cleaned["title"] = title

        description = cls._clean_text(meta.get("description"), max_length=260)
        if description and description != title and description not in cls.GENERIC_TITLES:
            cleaned["description"] = description

        return cleaned

    @classmethod
    def _collect_video_urls(cls, value: Any) -> list[str]:
        urls: list[str] = []

        def add(url: str) -> None:
            if (
                url.startswith(("http://", "https://"))
                and cls._looks_like_video_url(url)
                and url not in urls
            ):
                urls.append(url)

        def walk(item: Any) -> None:
            if isinstance(item, str):
                for match in cls.VIDEO_URL_RE.finditer(item):
                    add(match.group(0))
                add(item)
                return

            if isinstance(item, dict):
                for child in item.values():
                    walk(child)
                return

            if isinstance(item, list):
                for child in item:
                    walk(child)

        walk(value)
        return urls

    @classmethod
    def _merge_meta(cls, target: dict[str, str], source: dict[str, str]) -> None:
        for key, value in source.items():
            current = target.get(key)
            if value and (not current or cls._is_generic_title(current)):
                target[key] = value

    @classmethod
    def _find_primary_container(cls, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None

        for key in cls.PRIMARY_META_CONTAINER_KEYS:
            container = value.get(key)
            if isinstance(container, dict):
                return container

        for parent_key in cls.PRIMARY_CONTAINER_PARENT_KEYS:
            parent = value.get(parent_key)
            if isinstance(parent, dict):
                container = cls._find_primary_container(parent)
                if container:
                    return container

        return None

    @classmethod
    def _extract_primary_meta(cls, value: Any) -> dict[str, str]:
        container = cls._find_primary_container(value)
        if not isinstance(container, dict):
            return {}

        return cls._extract_metadata_from_dict(container, allow_body_text=True)

    @classmethod
    def _extract_primary_video_urls(cls, value: Any) -> list[str]:
        container = cls._find_primary_container(value)
        if not isinstance(container, dict):
            return []

        return cls._collect_video_urls(container)

    @classmethod
    def _extract_metadata_from_dict(
        cls,
        item: dict[str, Any],
        *,
        allow_body_text: bool = False,
    ) -> dict[str, str]:
        meta: dict[str, str] = {}

        for key in cls.TITLE_KEYS:
            title = cls._clean_text(item.get(key), max_length=120)
            if title and not cls._is_generic_title(title):
                meta["title"] = title
                break

        description_keys = (
            cls.PRIMARY_DESCRIPTION_KEYS
            if allow_body_text
            else cls.FALLBACK_DESCRIPTION_KEYS
        )
        for key in description_keys:
            desc = cls._clean_text(item.get(key), max_length=260)
            if desc and desc != meta.get("title") and desc not in cls.GENERIC_TITLES:
                meta["description"] = desc
                break

        return cls._clean_meta(meta)

    @classmethod
    def _make_result(
        cls,
        candidates: list[str],
        metadata: dict[str, str] | None = None,
        source: str | None = None,
    ) -> dict[str, Any] | None:
        if not candidates:
            return None

        result: dict[str, Any] = {
            "video_url": candidates[0],
            "meta": cls._clean_meta(metadata or {}),
        }
        if source:
            result["source"] = source
        return result

    @classmethod
    def _add_video_candidates(cls, candidates: list[str], value: Any) -> None:
        for video_url in cls._collect_video_urls(value):
            if video_url not in candidates:
                candidates.append(video_url)

    @staticmethod
    def _preferred_candidates(
        primary_candidates: list[str],
        fallback_candidates: list[str],
    ) -> list[str]:
        return primary_candidates or fallback_candidates

    @staticmethod
    def _is_browser_closed_error(error: Exception) -> bool:
        message = str(error).lower()
        return any(
            token in message
            for token in (
                "browser window has been closed",
                "target page, context or browser has been closed",
                "page has been closed",
                "context has been closed",
                "browser has been closed",
                "connection closed",
                "websocket is not connected",
                "not connected to devtools",
                "connection lost",
                "disconnected",
                "closed",
                "娴忚鍣ㄧ獥鍙ｅ凡鍏抽棴",
            )
        )

    @classmethod
    def _extract_link_id(cls, *values: str | None) -> str | None:
        for value in values:
            if not value:
                continue

            parsed = urlparse(value)
            query = parse_qs(parsed.query)
            for key in ("link_id", "linkid"):
                link_id = query.get(key, [None])[0]
                if link_id:
                    return link_id

            match = cls.LINK_ID_PATH_RE.search(parsed.path)
            if match:
                return match.group(1)

            decoded = unquote(value)
            match = cls.LINK_ID_TEXT_RE.search(decoded)
            if match:
                return match.group(1)

        return None

    @classmethod
    def _response_matches_link_id(cls, response_url: str, expected_link_id: str | None) -> bool:
        if not expected_link_id:
            return True
        return cls._extract_link_id(response_url) == expected_link_id

    @staticmethod
    def _html_attrs(tag: str) -> dict[str, str]:
        attrs: dict[str, str] = {}
        for match in re.finditer(r"""([:\w-]+)\s*=\s*(['"])(.*?)\2""", tag):
            attrs[match.group(1).lower()] = html.unescape(match.group(3))
        return attrs

    @classmethod
    def _extract_html_metadata(cls, text: str) -> dict[str, str]:
        meta: dict[str, str] = {}

        for tag in cls.META_TAG_RE.findall(text):
            attrs = cls._html_attrs(tag)
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            content = attrs.get("content")
            if not content:
                continue

            if key in {"og:title", "twitter:title", "title"} and not meta.get("title"):
                meta["title"] = content
            elif (
                key in {"description", "og:description", "twitter:description"}
                and not meta.get("description")
            ):
                meta["description"] = content

        if not meta.get("title"):
            match = cls.TITLE_TAG_RE.search(text)
            if match:
                meta["title"] = html.unescape(match.group(1))

        return cls._clean_meta(meta)

    @staticmethod
    def _response_json(response: requests.Response) -> Any | None:
        content_type = response.headers.get("content-type", "").lower()
        if "json" not in content_type and not response.text.lstrip().startswith(("{", "[")):
            return None

        try:
            return response.json()
        except ValueError:
            return None

    @classmethod
    def _state_value(cls, value: Any, *, max_length: int = 240) -> str | None:
        if value is None:
            return None
        return cls._clean_text(str(value), max_length=max_length)

    @staticmethod
    def _normalize_state_key(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())

    @staticmethod
    def _normalize_user_agent(value: str | None) -> str | None:
        if not value:
            return value
        return value.replace("HeadlessChrome/", "Chrome/")

    @staticmethod
    def _is_fast_cookie_domain(domain: str | None) -> bool:
        if not domain:
            return False
        normalized = domain.lstrip(".").lower()
        return normalized.endswith("xiaoheihe.cn")

    @classmethod
    def _extract_browser_storage_values(cls, snapshot: dict[str, Any] | None) -> dict[str, str]:
        values: dict[str, str] = {}
        if not isinstance(snapshot, dict):
            return values

        for storage_key in ("localStorage", "sessionStorage"):
            storage = snapshot.get(storage_key)
            if not isinstance(storage, dict):
                continue
            for raw_key, raw_value in storage.items():
                normalized_key = cls._normalize_state_key(str(raw_key))
                target_key = cls.FAST_STATE_STORAGE_KEYS.get(normalized_key)
                if not target_key:
                    continue
                cleaned = cls._state_value(raw_value, max_length=256)
                if cleaned and not values.get(target_key):
                    values[target_key] = cleaned

        return values

    @classmethod
    def _load_fast_state(cls) -> dict[str, Any]:
        path = cls._fast_state_path()
        state: dict[str, Any] = {}

        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                loaded = None

            if isinstance(loaded, dict):
                state = loaded

        changed = False
        if state.get("version") != cls.FAST_STATE_VERSION:
            state["version"] = cls.FAST_STATE_VERSION
            changed = True

        device_id = cls._state_value(state.get("device_id"), max_length=256)
        if not device_id:
            state["device_id"] = secrets.token_hex(16)
            changed = True

        if changed:
            cls._save_fast_state(state)

        return state

    @classmethod
    def _save_fast_state(cls, state: dict[str, Any]) -> None:
        path = cls._fast_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = dict(state)
        payload["version"] = cls.FAST_STATE_VERSION
        payload["saved_at"] = int(time.time())

        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temp_path.replace(path)

    @classmethod
    def _restore_fast_cookies(
        cls,
        session: requests.Session,
        state: dict[str, Any],
    ) -> int:
        applied = 0
        cookies = state.get("cookies")
        if not isinstance(cookies, list):
            return applied

        for item in cookies:
            if not isinstance(item, dict):
                continue

            name = cls._state_value(item.get("name"), max_length=160)
            value = cls._state_value(item.get("value"), max_length=4096)
            domain = cls._state_value(item.get("domain"), max_length=200)
            if not name or value is None or not cls._is_fast_cookie_domain(domain):
                continue

            kwargs: dict[str, Any] = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": cls._state_value(item.get("path"), max_length=200) or "/",
                "secure": bool(item.get("secure")),
            }
            expires = item.get("expires")
            if isinstance(expires, (int, float)) and expires > 0:
                kwargs["expires"] = int(expires)

            try:
                session.cookies.set_cookie(requests.cookies.create_cookie(**kwargs))
            except Exception:
                continue

            applied += 1

        return applied

    @classmethod
    def _new_fast_session(cls) -> tuple[requests.Session, dict[str, Any]]:
        state = cls._load_fast_state()
        session = requests.Session()
        session.headers.update(cls.FAST_HEADERS)

        user_agent = cls._normalize_user_agent(
            cls._state_value(state.get("user_agent"), max_length=400)
        )
        if user_agent:
            session.headers["User-Agent"] = user_agent

        restored_cookies = cls._restore_fast_cookies(session, state)
        LOGGER.info(
            "heybox fast session ready cookies=%s has_heybox_id=%s has_device_id=%s",
            restored_cookies,
            bool(cls._state_value(state.get("heybox_id"), max_length=256)),
            bool(cls._state_value(state.get("device_id"), max_length=256)),
        )
        return session, state

    @classmethod
    def _fast_get(
        cls,
        session: requests.Session,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> requests.Response | None:
        try:
            response = session.get(
                url,
                headers=headers,
                timeout=cls.FAST_REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response
        except requests.RequestException:
            return None

    @classmethod
    def _scan_response(
        cls,
        response: requests.Response,
        primary_candidates: list[str],
        fallback_candidates: list[str],
        metadata: dict[str, str],
        *,
        expected_link_id: str | None = None,
    ) -> bool:
        cls._add_video_candidates(fallback_candidates, response.url)

        data = cls._response_json(response)
        if data is not None:
            if isinstance(data, dict) and data.get("status") == "show_captcha":
                return True
            if cls._is_post_detail_response(response.url):
                if cls._response_matches_link_id(response.url, expected_link_id):
                    cls._add_video_candidates(
                        primary_candidates,
                        cls._extract_primary_video_urls(data),
                    )
                    cls._merge_meta(metadata, cls._extract_metadata(data))
            else:
                cls._add_video_candidates(fallback_candidates, data)
            return False

        text = response.text
        cls._add_video_candidates(fallback_candidates, text)
        cls._merge_meta(metadata, cls._extract_html_metadata(text))
        return "show_captcha" in text or "captcha_required" in text

    @staticmethod
    def _hkey_mix(value: int) -> int:
        return ((value << 1) ^ 27) & 255 if value & 128 else value << 1

    @classmethod
    def _hkey_qm(cls, value: int) -> int:
        return cls._hkey_mix(value) ^ value

    @classmethod
    def _hkey_dollar(cls, value: int) -> int:
        return cls._hkey_qm(cls._hkey_mix(value))

    @classmethod
    def _hkey_ym(cls, value: int) -> int:
        return cls._hkey_dollar(cls._hkey_qm(cls._hkey_mix(value)))

    @classmethod
    def _hkey_gm(cls, value: int) -> int:
        return cls._hkey_ym(value) ^ cls._hkey_dollar(value) ^ cls._hkey_qm(value)

    @classmethod
    def _hkey_km(cls, values: list[int]) -> list[int]:
        if len(values) < 4:
            values = values + [0] * (4 - len(values))

        mixed = [
            cls._hkey_gm(values[0])
            ^ cls._hkey_ym(values[1])
            ^ cls._hkey_dollar(values[2])
            ^ cls._hkey_qm(values[3]),
            cls._hkey_qm(values[0])
            ^ cls._hkey_gm(values[1])
            ^ cls._hkey_ym(values[2])
            ^ cls._hkey_dollar(values[3]),
            cls._hkey_dollar(values[0])
            ^ cls._hkey_qm(values[1])
            ^ cls._hkey_gm(values[2])
            ^ cls._hkey_ym(values[3]),
            cls._hkey_ym(values[0])
            ^ cls._hkey_dollar(values[1])
            ^ cls._hkey_qm(values[2])
            ^ cls._hkey_gm(values[3]),
        ]
        return mixed + values[4:]

    @classmethod
    def _hkey_av(cls, value: str, end_offset: int) -> str:
        alphabet = cls.HKEY_ALPHABET[:end_offset]
        return "".join(alphabet[ord(char) % len(alphabet)] for char in value)

    @classmethod
    def _hkey_sv(cls, value: str) -> str:
        return "".join(cls.HKEY_ALPHABET[ord(char) % len(cls.HKEY_ALPHABET)] for char in value)

    @staticmethod
    def _interleave(values: list[str]) -> str:
        output = []
        for index in range(max(len(value) for value in values)):
            for value in values:
                if index < len(value):
                    output.append(value[index])
        return "".join(output)

    @classmethod
    def _hkey_ov(cls, path: str, timestamp: int, nonce: str) -> str:
        normalized_path = "/" + "/".join(part for part in path.split("/") if part) + "/"
        seed = cls._interleave(
            [
                cls._hkey_av(str(timestamp), -2),
                cls._hkey_sv(normalized_path),
                cls._hkey_sv(nonce),
            ]
        )[:20]
        digest = hashlib.md5(seed.encode()).hexdigest()
        check = str(sum(cls._hkey_km([ord(char) for char in digest[-6:]])) % 100).zfill(2)
        prefix = cls._hkey_av(digest[:5], -4)
        return f"{prefix}{check}"

    @classmethod
    def _heybox_web_signature(cls, path: str) -> dict[str, str]:
        timestamp = int(time.time())
        nonce_seed = f"{timestamp}{secrets.token_hex(16)}"
        nonce = hashlib.md5(nonce_seed.encode()).hexdigest().upper()
        return {
            "hkey": cls._hkey_ov(path, timestamp + 1, nonce),
            "_time": str(timestamp),
            "nonce": nonce,
        }

    @classmethod
    def _fetch_post_api(
        cls,
        session: requests.Session,
        fast_state: dict[str, Any],
        source_url: str,
        page_url: str,
        link_id: str,
        primary_candidates: list[str],
        fallback_candidates: list[str],
        metadata: dict[str, str],
    ) -> bool:
        source_query = parse_qs(urlparse(source_url).query)
        page_query = parse_qs(urlparse(page_url).query)
        user_agent = cls._normalize_user_agent(
            cls._state_value(fast_state.get("user_agent"), max_length=400)
        )
        heybox_id = cls._state_value(fast_state.get("heybox_id"), max_length=256) or ""
        device_id = cls._state_value(fast_state.get("device_id"), max_length=256) or secrets.token_hex(16)

        params = {
            "os_type": "web",
            "app": "heybox",
            "client_type": "web",
            "version": "999.0.4",
            "web_version": "2.5",
            "x_client_type": "web",
            "x_app": "heybox_website",
            "heybox_id": heybox_id,
            "x_os_type": "Windows",
            "device_info": user_agent or "Chrome/125.0.0.0",
            "device_id": device_id,
            "link_id": link_id,
            "is_first": "1",
            "page": "1",
            "index": "1",
            "limit": "20",
            "owner_only": "0",
        }
        for key in ("h_src", "h_camp", "h_session_id"):
            value = source_query.get(key, [None])[0] or page_query.get(key, [None])[0]
            if value:
                params[key] = value
        params.update(cls._heybox_web_signature("/bbs/app/link/tree"))
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.xiaoheihe.cn",
            "Referer": page_url,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "X-Requested-With": "XMLHttpRequest",
        }
        if user_agent:
            headers["User-Agent"] = user_agent

        try:
            response = session.get(
                "https://api.xiaoheihe.cn/bbs/app/link/tree",
                params=params,
                headers=headers,
                timeout=cls.FAST_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except requests.RequestException:
            return False

        return cls._scan_response(
            response,
            primary_candidates,
            fallback_candidates,
            metadata,
            expected_link_id=link_id,
        )

    @classmethod
    def _log_fast_outcome(
        cls,
        url: str,
        stage: str,
        *,
        result: dict[str, Any] | None = None,
        captcha: bool = False,
    ) -> None:
        link_id = cls._extract_link_id(url) or "-"
        if result:
            LOGGER.info("heybox parse fast success link_id=%s stage=%s", link_id, stage)
            return
        if captcha:
            LOGGER.info("heybox parse fast blocked link_id=%s stage=%s reason=show_captcha", link_id, stage)
            return
        LOGGER.info("heybox parse fast miss link_id=%s stage=%s", link_id, stage)

    @classmethod
    def _scan_scripts(
        cls,
        session: requests.Session,
        page_url: str,
        page_html: str,
        primary_candidates: list[str],
        fallback_candidates: list[str],
        metadata: dict[str, str],
        *,
        expected_link_id: str | None = None,
    ) -> bool:
        scanned = 0
        for src in cls.SCRIPT_SRC_RE.findall(page_html):
            if scanned >= cls.FAST_SCRIPT_LIMIT or primary_candidates:
                break

            script_url = urljoin(page_url, html.unescape(src.strip()))
            if not script_url.startswith(("http://", "https://")):
                continue

            response = cls._fast_get(session, script_url, headers={"Referer": page_url})
            if response is None:
                continue

            scanned += 1
            if cls._scan_response(
                response,
                primary_candidates,
                fallback_candidates,
                metadata,
                expected_link_id=expected_link_id,
            ):
                return True

        return False

    @classmethod
    def _save_browser_session_state(cls, page) -> None:
        try:
            storage_state = page.context.storage_state()
        except PlaywrightError:
            storage_state = None

        try:
            browser_snapshot = page.evaluate(
                """() => {
                    const dumpStorage = (storage) => {
                        const output = {};
                        for (let index = 0; index < storage.length; index += 1) {
                            const key = storage.key(index);
                            if (key !== null) {
                                output[key] = storage.getItem(key);
                            }
                        }
                        return output;
                    };

                    return {
                        href: location.href,
                        origin: location.origin,
                        userAgent: navigator.userAgent,
                        localStorage: dumpStorage(window.localStorage),
                        sessionStorage: dumpStorage(window.sessionStorage),
                    };
                }"""
            )
        except PlaywrightError:
            browser_snapshot = None

        cookies: list[dict[str, Any]] = []
        if isinstance(storage_state, dict):
            raw_cookies = storage_state.get("cookies")
            if isinstance(raw_cookies, list):
                cookies = [
                    item
                    for item in raw_cookies
                    if isinstance(item, dict)
                    and cls._is_fast_cookie_domain(
                        cls._state_value(item.get("domain"), max_length=200)
                    )
                ]

        cls._save_browser_session_snapshot(
            cookies=cookies,
            browser_snapshot=browser_snapshot,
        )

    @classmethod
    def _save_browser_session_snapshot(
        cls,
        *,
        cookies: list[dict[str, Any]] | None = None,
        browser_snapshot: dict[str, Any] | None = None,
    ) -> None:
        cookies = cookies or []

        state = cls._load_fast_state()
        state["cookies"] = cookies

        if isinstance(browser_snapshot, dict):
            state["page_url"] = browser_snapshot.get("href")
            state["page_origin"] = browser_snapshot.get("origin")
            state["user_agent"] = cls._normalize_user_agent(
                cls._state_value(browser_snapshot.get("userAgent"), max_length=400)
            )
            state["storage"] = {
                "localStorage": browser_snapshot.get("localStorage"),
                "sessionStorage": browser_snapshot.get("sessionStorage"),
            }

        for key, value in cls._extract_browser_storage_values(browser_snapshot).items():
            if value:
                state[key] = value

        cls._save_fast_state(state)
        LOGGER.info(
            "heybox fast session refreshed cookies=%s has_heybox_id=%s has_device_id=%s",
            len(cookies),
            bool(cls._state_value(state.get("heybox_id"), max_length=256)),
            bool(cls._state_value(state.get("device_id"), max_length=256)),
        )

    @classmethod
    def _scrape_fast(cls, url: str) -> dict[str, Any] | None:
        session, fast_state = cls._new_fast_session()
        primary_candidates: list[str] = []
        fallback_candidates: list[str] = []
        metadata: dict[str, str] = {}
        link_id = cls._extract_link_id(url)

        response = cls._fast_get(session, url)
        if response is None:
            cls._log_fast_outcome(url, "share_request")
            return None

        captcha_required = cls._scan_response(
            response,
            primary_candidates,
            fallback_candidates,
            metadata,
            expected_link_id=link_id,
        )
        candidates = cls._preferred_candidates(primary_candidates, fallback_candidates)
        if candidates and (primary_candidates or not captcha_required):
            result = cls._make_result(candidates, metadata, source="fast")
            cls._log_fast_outcome(url, "share_page", result=result)
            return result

        page_url = response.url
        page_html = response.text
        link_id = cls._extract_link_id(url, page_url, page_html)

        if link_id:
            captcha_required = (
                cls._fetch_post_api(
                    session,
                    fast_state,
                    url,
                    page_url,
                    link_id,
                    primary_candidates,
                    fallback_candidates,
                    metadata,
                )
                or captcha_required
            )
            candidates = cls._preferred_candidates(primary_candidates, fallback_candidates)
            if candidates and (primary_candidates or not captcha_required):
                result = cls._make_result(candidates, metadata, source="fast")
                cls._log_fast_outcome(url, "post_api", result=result)
                return result

        captcha_required = cls._scan_scripts(
            session,
            page_url,
            page_html,
            primary_candidates,
            fallback_candidates,
            metadata,
            expected_link_id=link_id,
        ) or captcha_required
        candidates = cls._preferred_candidates(primary_candidates, fallback_candidates)
        if candidates and (primary_candidates or not captcha_required):
            result = cls._make_result(candidates, metadata, source="fast")
            cls._log_fast_outcome(url, "script_scan", result=result)
            return result

        cls._log_fast_outcome(url, "final", captcha=captcha_required)
        return None

    @classmethod
    def _extract_metadata(cls, value: Any) -> dict[str, str]:
        meta = cls._extract_primary_meta(value)
        if meta.get("title") and meta.get("description"):
            return meta

        def walk(item: Any) -> None:
            if isinstance(item, dict):
                partial = cls._extract_metadata_from_dict(item, allow_body_text=False)
                cls._merge_meta(meta, partial)
                if meta.get("title") and meta.get("description"):
                    return

                for key, child in item.items():
                    if key.lower() in cls.META_SKIP_KEYS:
                        continue
                    walk(child)
                    if meta.get("title") and meta.get("description"):
                        return
                return

            if isinstance(item, list):
                for child in item:
                    walk(child)
                    if meta.get("title") and meta.get("description"):
                        return

        walk(value)
        return cls._clean_meta(meta)

    @classmethod
    def _scrape_on_page(cls, page, url: str) -> dict[str, Any] | None:
        link_id = cls._extract_link_id(url)
        primary_candidates: list[str] = []
        fallback_candidates: list[str] = []
        metadata: dict[str, str] = {}
        captcha_required = False

        def add_candidate(candidates: list[str], video_url: str) -> None:
            if video_url not in candidates:
                candidates.append(video_url)

        def on_response(response):
            nonlocal captcha_required

            response_url = response.url
            content_type = response.headers.get("content-type", "").lower()

            if cls._looks_like_video_url(response_url) or any(
                marker in content_type for marker in cls.VIDEO_CONTENT_TYPES
            ):
                add_candidate(fallback_candidates, response_url)

            if not any(
                marker in content_type
                for marker in ("application/json", "text/", "javascript")
            ):
                return

            try:
                if "application/json" in content_type:
                    data = response.json()
                    if data.get("status") == "show_captcha":
                        captcha_required = True
                    if cls._is_post_detail_response(response_url) and cls._response_matches_link_id(
                        response_url,
                        link_id,
                    ):
                        cls._add_video_candidates(
                            primary_candidates,
                            cls._extract_primary_video_urls(data),
                        )
                        cls._merge_meta(metadata, cls._extract_metadata(data))
            except Exception:
                return

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(PLAYWRIGHT_TIMEOUT)

            if captcha_required and not PLAYWRIGHT_HEADLESS and not primary_candidates:
                LOGGER.info(
                    "heybox parse browser waiting_captcha link_id=%s timeout_ms=%s",
                    link_id or "-",
                    CAPTCHA_MANUAL_TIMEOUT,
                )
                elapsed = 0
                try:
                    while elapsed < CAPTCHA_MANUAL_TIMEOUT and not primary_candidates:
                        page.wait_for_timeout(1000)
                        elapsed += 1000
                except PlaywrightError:
                    pass
        finally:
            try:
                page.remove_listener("response", on_response)
            except PlaywrightError:
                pass

        try:
            cls._save_browser_session_state(page)
        except Exception:
            LOGGER.exception("heybox fast session refresh failed link_id=%s", link_id or "-")

        if captcha_required and not primary_candidates:
            LOGGER.info(
                "heybox parse browser blocked link_id=%s reason=show_captcha",
                link_id or "-",
            )
            raise cls.CaptchaRequiredError("小黑盒返回验证码，无法自动解析该帖子")

        candidates = cls._preferred_candidates(primary_candidates, fallback_candidates)
        if candidates:
            LOGGER.info(
                "heybox parse browser success link_id=%s source=%s",
                link_id or "-",
                "primary" if primary_candidates else "fallback",
            )
            return {
                "video_url": candidates[0],
                "meta": cls._clean_meta(metadata),
                "source": "browser",
            }

        LOGGER.info("heybox parse browser miss link_id=%s", link_id or "-")
        return None
