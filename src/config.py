from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
from typing import Any

from .constants import APP, CONFIG_NAME, DB_NAME, LOG_NAME, UserError


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # __file__ is src/config.py → parent is src/ → parent is project root
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return app_dir() / CONFIG_NAME


def log_path() -> Path:
    return app_dir() / LOG_NAME


def setup_logging() -> logging.Logger:
    logger = logging.getLogger(APP)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    try:
        handler = RotatingFileHandler(log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    except (OSError, ValueError):
        logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


LOGGER = setup_logging()


def log_exception(context: str, exc: BaseException) -> None:
    LOGGER.exception("%s: %s", context, exc)


def _excepthook(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
    LOGGER.exception("Unhandled exception", exc_info=(exc_type, exc, tb))
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _excepthook


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_default_config(path: Path | None = None) -> None:
    resolved_path = config_path() if path is None else path
    if resolved_path.exists():
        return
    data = {
        "db_path": DB_NAME,
        "gallery_dl_command": None,
        "metadata_worker_limit": 5,
    }
    resolved_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def default_db_path() -> Path:
    cfg = load_config()
    configured = cfg.get("db_path")
    if configured:
        p = Path(str(configured))
        return p if p.is_absolute() else app_dir() / p
    return app_dir() / DB_NAME


def parse_global_options(argv: list[str]) -> tuple[Path, bool, list[str]]:
    db_path: Path | None = None
    quiet = False
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-d", "--db"):
            if i + 1 >= len(argv):
                raise UserError("--db requires a path")
            db_path = Path(argv[i + 1]).expanduser()
            i += 2
            continue
        if arg.startswith("--db="):
            db_path = Path(arg.split("=", 1)[1]).expanduser()
            i += 1
            continue
        if arg in ("-q", "--quiet"):
            quiet = True
            i += 1
            continue
        rest.append(arg)
        i += 1
    return db_path or default_db_path(), quiet, rest
