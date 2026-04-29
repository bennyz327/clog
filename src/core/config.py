from __future__ import annotations

import atexit
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
from typing import Any

from .constants import APP, CONFIG_NAME, DB_DIR_NAME, DB_NAME, LOG_NAME, UserError


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # __file__ is src/core/config.py → 3 levels up is project root
    return Path(__file__).resolve().parents[2]


def db_dir() -> Path:
    """User-data directory: holds the sqlite db, config, log. Created on first
    write (first launch). Hydrus uses the same `db/` convention next to the exe."""
    return app_dir() / DB_DIR_NAME


def _ensure_db_dir() -> bool:
    try:
        db_dir().mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


def config_path() -> Path:
    return db_dir() / CONFIG_NAME


def log_path() -> Path:
    return db_dir() / LOG_NAME


def _base_logger() -> logging.Logger:
    logger = logging.getLogger(APP)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


def setup_logging() -> logging.Logger:
    logger = _base_logger()
    if logger.handlers:
        non_null_handlers = [handler for handler in logger.handlers if not isinstance(handler, logging.NullHandler)]
        if non_null_handlers:
            return logger
    try:
        if not _ensure_db_dir():
            raise OSError(f"could not create {db_dir()}")
        handler = RotatingFileHandler(log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.handlers = [existing for existing in logger.handlers if not isinstance(existing, logging.NullHandler)]
        logger.addHandler(handler)
    except (OSError, ValueError):
        logger.handlers = [existing for existing in logger.handlers if not isinstance(existing, logging.NullHandler)]
        logger.addHandler(logging.NullHandler())
    return logger


def shutdown_logging() -> None:
    logger = logging.getLogger(APP)
    handlers = list(logger.handlers)
    for handler in handlers:
        handler.close()
        logger.removeHandler(handler)
    logger.addHandler(logging.NullHandler())


class _LazyLoggerProxy:
    def _logger(self) -> logging.Logger:
        return setup_logging()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._logger(), name)


LOGGER = _LazyLoggerProxy()
atexit.register(shutdown_logging)


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
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "db_path": DB_NAME,
        "gallery_dl_command": None,
        "metadata_worker_limit": 5,
    }
    resolved_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_legacy_config() -> dict[str, Any]:
    return load_config()


def default_db_path() -> Path:
    return db_dir() / DB_NAME


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
