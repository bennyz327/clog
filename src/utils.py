from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .constants import UserError


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def today() -> dt.date:
    return dt.date.today()


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHFJA-Z]|\x1b][^\x07]*\x07")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


class CaptureIO(io.StringIO):
    def reconfigure(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    raw = url.strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if not parts.scheme and parts.netloc == "":
        parts = urlsplit("https://" + raw)
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = re.sub(r"/+$", "", parts.path or "")
    kept_query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lk = key.lower()
        if lk.startswith("utm_") or lk in {"fbclid", "gclid", "igshid"}:
            continue
        kept_query.append((key, value))
    query = urlencode(kept_query, doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def is_url(value: str) -> bool:
    if re.match(r"^https?://", value, flags=re.I):
        return True
    if " " in value:
        return False
    parts = urlsplit(value if "://" in value else "https://" + value)
    return "." in parts.netloc and bool(parts.netloc)


def infer_platform_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url if "://" in url else "https://" + url)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    mappings = [
        ("x.com", "x"),
        ("twitter.com", "x"),
        ("pixiv.net", "pixiv"),
        ("fanbox.cc", "fanbox"),
        ("patreon.com", "patreon"),
        ("fantia.jp", "fantia"),
        ("instagram.com", "instagram"),
        ("bsky.app", "bluesky"),
        ("skeb.jp", "skeb"),
        ("ci-en.net", "ci-en"),
        ("booth.pm", "booth"),
        ("gumroad.com", "gumroad"),
        ("subscribestar", "subscribestar"),
    ]
    for needle, platform in mappings:
        if needle in host:
            return platform
    if host:
        return host.split(".")[0]
    return None


def normalize_platform(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().lower() or None


def normalize_pid(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def shorten(value: Any, limit: int = 120) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def unique_messages(messages: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for message in messages:
        message = message.strip()
        if message and message not in seen:
            seen.add(message)
            result.append(message)
    return result


def prompt_input(prompt: str, default: str = "") -> str:
    try:
        return input(prompt)
    except EOFError:
        return default


def parse_when(value: str) -> tuple[str, int | None]:
    raw = value.strip().lower()
    base = today()
    if raw == "today":
        return base.isoformat(), None
    if raw == "tomorrow":
        return (base + dt.timedelta(days=1)).isoformat(), None
    match = re.fullmatch(r"(\d+)d", raw)
    if match:
        days = int(match.group(1))
        return (base + dt.timedelta(days=days)).isoformat(), days
    try:
        parsed = dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise UserError("WHEN must be YYYY-MM-DD, today, tomorrow, or Nd") from exc
    return parsed.isoformat(), None


def split_values(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"[\r\n,]+", raw)
    return [part.strip() for part in parts if part.strip()]


def make_parser(prog: str) -> argparse.ArgumentParser:
    return argparse.ArgumentParser(prog=prog, add_help=True, allow_abbrev=False)


def parse_known(parser: argparse.ArgumentParser, args: list[str]) -> argparse.Namespace:
    return parser.parse_args(args)
