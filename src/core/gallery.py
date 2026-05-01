from __future__ import annotations

import importlib.util
import os
from contextlib import redirect_stderr, redirect_stdout
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

from db import read_app_setting
from .config import LOGGER, app_dir, default_db_path
from .utils import (
    CaptureIO,
    infer_platform_from_url,
    normalize_pid,
    normalize_platform,
    shorten,
    strip_ansi,
    unique_messages,
)

_gallery_dl = None
_gallery_extractor = None

PROFILE_LIKE_SUBCATEGORIES = {
    "user",
    "creator",
    "profile",
    "timeline",
    "tweets",
    "media",
    "artworks",
    "following",
    "followers",
    "avatar",
    "background",
    "info",
    "likes",
    "highlights",
}
POST_LIKE_SUBCATEGORIES = {
    "post",
    "tweet",
    "work",
    "status",
    "quote",
}
PROFILE_LIKE_CLASS_TOKENS = ("userextractor", "creatorextractor", "profileextractor")
POST_LIKE_CLASS_TOKENS = (
    "postextractor",
    "tweetextractor",
    "workextractor",
    "statusextractor",
    "quoteextractor",
)


def _find_gallery_dl_exe() -> str | None:
    exe_name = "gallery-dl.exe" if os.name == "nt" else "gallery-dl"
    candidates: list[Path] = [app_dir() / exe_name]
    if os.name == "nt":
        for env_key in ("APPDATA", "LOCALAPPDATA"):
            base = os.environ.get(env_key)
            if not base:
                continue
            base_path = Path(base)
            for scripts_dir in base_path.glob("Python/Python3*/Scripts"):
                candidates.append(scripts_dir / exe_name)
            for scripts_dir in base_path.glob("Programs/Python/Python3*/Scripts"):
                candidates.append(scripts_dir / exe_name)
    else:
        candidates.append(Path.home() / ".local" / "bin" / exe_name)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def gallery_external_command() -> list[str] | None:
    configured = _system_setting("gallery_dl_command")
    if configured:
        if isinstance(configured, list):
            return [str(x) for x in configured]
        return [str(configured)]
    exe = shutil.which("gallery-dl") or _find_gallery_dl_exe()
    if exe:
        return [exe]
    return None


def _system_setting(key: str, default: Any = None) -> Any:
    try:
        db_path = Path(os.environ.get("CLOG_DB_PATH") or default_db_path())
        return read_app_setting(db_path, "system", key, default)
    except Exception:
        return default


def gallery_module_available() -> bool:
    return _gallery_dl is not None or importlib.util.find_spec("gallery_dl") is not None


def gallery_available() -> bool:
    return gallery_external_command() is not None or gallery_module_available()


def _load_gallery_extractor() -> Any | None:
    global _gallery_extractor
    if _gallery_extractor is not None:
        return _gallery_extractor
    if not gallery_module_available():
        return None
    try:
        from gallery_dl import extractor as gallery_extractor_module
    except Exception:
        return None
    _gallery_extractor = gallery_extractor_module
    return _gallery_extractor


def classify_url_kind(url: str) -> str:
    extractor_module = _load_gallery_extractor()
    if extractor_module is None:
        return "unknown"
    try:
        extractor = extractor_module.find(url)
    except Exception:
        return "unknown"
    if extractor is None:
        return "unknown"
    subcategory = normalize_platform(getattr(extractor, "subcategory", None))
    class_name = extractor.__class__.__name__.lower()
    if subcategory in POST_LIKE_SUBCATEGORIES:
        return "post-like"
    if subcategory in PROFILE_LIKE_SUBCATEGORIES:
        return "profile-like"
    if any(token in class_name for token in POST_LIKE_CLASS_TOKENS):
        return "post-like"
    if any(token in class_name for token in PROFILE_LIKE_CLASS_TOKENS):
        return "profile-like"
    return "unknown"


def parse_json_stream(output: str) -> list[Any]:
    output = output.strip()
    if not output:
        return []
    try:
        value = json.loads(output)
        return value if isinstance(value, list) else [value]
    except json.JSONDecodeError:
        pass
    values: list[Any] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            values.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return values


def run_gallery_metadata(
    url: str,
    timeout: int = 60,
    *,
    quick: bool = False,
    live_status: Any = None,
) -> tuple[list[Any], str | None]:
    cmd = gallery_external_command()
    errors: list[str] = []
    range_flags = ["--post-range", "1"] if quick else []
    if cmd:
        attempts = [
            cmd + ["--dump-json"] + range_flags + [url],
            cmd + ["-j"] + range_flags + [url],
        ]
        _win_flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        for command in attempts:
            try:
                proc = subprocess.Popen(
                    command,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **_win_flags,
                )
            except OSError as exc:
                errors.append(str(exc))
                continue
            if live_status is not None:
                live_status.set_status_text(f"正在以 gallery-dl 擷取：{shorten(url, 80)}")
                live_status.set_terminate_callable(lambda p=proc: _terminate_process(p))
            start = time.monotonic()
            while proc.poll() is None:
                if live_status is not None and live_status.cancel_requested():
                    _terminate_process(proc)
                    if live_status is not None:
                        live_status.set_terminate_callable(None)
                    return [], "使用者已取消"
                if timeout > 0 and (time.monotonic() - start) >= timeout:
                    _terminate_process(proc)
                    if live_status is not None:
                        live_status.set_terminate_callable(None)
                    errors.append("gallery-dl 執行逾時")
                    break
                time.sleep(0.1)
            if proc.poll() is None:
                continue
            stdout, stderr = proc.communicate()
            if live_status is not None:
                live_status.set_terminate_callable(None)
            values = parse_json_stream(stdout or "")
            if values:
                return values, None
            raw_diag = strip_ansi(stderr or stdout or "")
            if proc.returncode != 0:
                msg = shorten(raw_diag or f"exit {proc.returncode}", 500)
            else:
                msg = "gallery-dl returned no JSON" + (f": {shorten(raw_diag, 200)}" if raw_diag else "")
            LOGGER.info("gallery-dl no output url=%s flag=%s: %s", url, command[-2] if len(command) >= 2 else "", msg)
            errors.append(msg)
    if gallery_module_available():
        values, error = run_gallery_module(url, quick=quick)
        if values:
            return values, None
        if error:
            errors.append(error)
    if not errors:
        return [], "gallery-dl 無法使用"
    errors = unique_messages(errors)
    return [], "; ".join(errors) if errors else "gallery-dl 執行失敗"


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    try:
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            return


def run_gallery_module(url: str, *, quick: bool = False) -> tuple[list[Any], str | None]:
    global _gallery_dl
    if _gallery_dl is None:
        try:
            import gallery_dl as imported_gallery_dl
        except Exception as exc:
            return [], str(exc)
        _gallery_dl = imported_gallery_dl
    cap_out = CaptureIO()
    cap_err = CaptureIO()
    old_argv = sys.argv[:]
    argv = ["gallery-dl", "-j"]
    if quick:
        argv += ["--post-range", "1"]
    argv.append(url)
    try:
        sys.argv = argv
        with redirect_stdout(cap_out), redirect_stderr(cap_err):
            code = _gallery_dl.main()
    except SystemExit as exc:
        code = exc.code
    except Exception as exc:
        return [], str(exc)
    finally:
        sys.argv = old_argv[:]
    out = cap_out.getvalue()
    err = strip_ansi(cap_err.getvalue())
    values = parse_json_stream(out)
    if values:
        return values, None
    if code not in (None, 0):
        msg = shorten(err or strip_ansi(out) or f"exit {code}", 500)
    else:
        hint = shorten(err or strip_ansi(out), 200)
        msg = "gallery-dl module returned no JSON" + (f": {hint}" if hint else "")
    LOGGER.info("gallery-dl module no output url=%s: %s", url, msg)
    return [], msg


def first_value(obj: Any, keys: list[str]) -> Any:
    if isinstance(obj, list):
        for item in obj:
            value = first_value(item, keys)
            if value not in (None, ""):
                return value
        return None
    if not isinstance(obj, dict):
        return None
    lower = {str(k).lower(): v for k, v in obj.items()}
    for key in keys:
        if key.lower() in lower and lower[key.lower()] not in (None, ""):
            return lower[key.lower()]
    for val in obj.values():
        if isinstance(val, (dict, list)):
            found = first_value(val, keys)
            if found not in (None, ""):
                return found
    return None


def gallery_platform(metadata: list[Any], url: str) -> str | None:
    value = first_value(
        metadata,
        ["_extractor", "extractor", "extractor_key", "category", "site", "platform"],
    )
    if isinstance(value, str):
        value = value.split(".")[0]
        value = value.split()[0]
        return normalize_platform(value)
    return infer_platform_from_url(url)


def extract_metadata(metadata: list[Any], url: str) -> dict[str, Any]:
    platform = gallery_platform(metadata, url)

    _creator_sub = first_value(metadata, ["creator", "user", "author", "uploader", "artist", "owner", "channel", "account"])
    platform_id: Any = None
    if isinstance(_creator_sub, dict) and _creator_sub.get("id") not in (None, ""):
        platform_id = _creator_sub["id"]
    if platform_id in (None, ""):
        platform_id = first_value(
            metadata,
            ["patreon_id", "pixiv_id", "fanbox_id", "twitter_id",
             "author_id", "creator_id", "artist_id",
             "owner_id", "uploader_id", "channel_id", "account_id", "profile_id",
             "fanclub_user_id", "fanclub_id"],
        )

    author_name = first_value(
        metadata,
        ["full_name", "display_name", "author", "author_name", "username", "user_name",
         "artist_name", "creator_name", "uploader", "channel", "owner",
         "fanclub_user_name"],
    )
    if author_name in (None, ""):
        sub = first_value(metadata, ["creator", "user", "author", "uploader", "artist", "owner"])
        if isinstance(sub, dict):
            for _k in ("full_name", "display_name", "name", "username"):
                if sub.get(_k) not in (None, ""):
                    author_name = sub[_k]
                    break

    profile_url = first_value(
        metadata,
        ["author_url", "user_url", "profile_url", "uploader_url", "artist_url", "creator_url", "channel_url",
         "fanclub_url"],
    )
    if profile_url in (None, ""):
        sub = first_value(metadata, ["creator", "user", "author", "uploader", "artist", "owner"])
        if isinstance(sub, dict):
            profile_url = sub.get("url") or sub.get("profile_url")
    if profile_url in (None, ""):
        if platform == "pixiv" and platform_id not in (None, ""):
            profile_url = f"https://www.pixiv.net/users/{platform_id}"
        elif platform == "fanbox":
            creator_id_str = first_value(metadata, ["creatorId", "creator_id"])
            if creator_id_str not in (None, ""):
                profile_url = f"https://{creator_id_str}.fanbox.cc"
            elif platform_id not in (None, ""):
                profile_url = f"https://www.fanbox.cc/@{platform_id}"

    post_id = first_value(metadata, ["post_id", "id", "submission_id", "tweet_id", "entry_id", "file_id", "pid"])
    title = first_value(metadata, ["title", "caption", "headline", "subject"])
    text = first_value(metadata, ["description", "content", "text", "body", "comment"])
    posted_at = first_value(metadata, ["date", "datetime", "created_at", "published_at", "timestamp", "time"])

    return {
        "platform": normalize_platform(str(platform)) if platform else None,
        "platform_id": normalize_pid(platform_id),
        "author_name": str(author_name) if author_name not in (None, "") else None,
        "profile_url": str(profile_url) if profile_url not in (None, "") else None,
        "post_id": str(post_id) if post_id not in (None, "") else None,
        "title": str(title) if title not in (None, "") else None,
        "text": str(text) if text not in (None, "") else None,
        "posted_at": str(posted_at) if posted_at not in (None, "") else None,
        "raw": metadata,
    }
