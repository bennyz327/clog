from __future__ import annotations

from pathlib import Path
from typing import Any

from core.config import LOGGER
from core.enrichment import SubprocessDriver
from core.utils import make_parser, parse_known
from db import connect
from .commands import (
    cmd_add,
    cmd_due,
    cmd_init,
    cmd_ls,
    cmd_name,
    cmd_post,
    cmd_remind,
    cmd_search,
    cmd_show,
    cmd_url,
    cmd_work,
)
from .printers import print_help


CommandHandler = Any

COMMANDS: dict[str, tuple[str, CommandHandler]] = {
    "init": ("init", cmd_init),
    "i": ("init", cmd_init),
    "add": ("add", cmd_add),
    "a": ("add", cmd_add),
    "name": ("name", cmd_name),
    "n": ("name", cmd_name),
    "url": ("url", cmd_url),
    "u": ("url", cmd_url),
    "post": ("post", cmd_post),
    "p": ("post", cmd_post),
    "remind": ("remind", cmd_remind),
    "r": ("remind", cmd_remind),
    "due": ("due", cmd_due),
    "d": ("due", cmd_due),
    "ls": ("ls", cmd_ls),
    "l": ("ls", cmd_ls),
    "work": ("work", cmd_work),
    "c": ("work", cmd_work),
    "search": ("search", cmd_search),
    "s": ("search", cmd_search),
    "show": ("show", cmd_show),
    "v": ("show", cmd_show),
}


def dispatch(db_path: Path, _quiet: bool, argv: list[str]) -> int:
    LOGGER.info("Dispatch db=%s argv=%s", db_path, argv)
    if argv and argv[0] == "__meta_worker":
        parser = make_parser("clog __meta_worker")
        parser.add_argument("--limit", type=int, default=5)
        ns = parse_known(parser, argv[1:])
        SubprocessDriver.process(db_path, ns.limit)
        return 0

    if argv and argv[0] in ("-h", "--help", "help"):
        print_help()
        return 0
    if not argv:
        print_help()
        return 0
    if len(argv) >= 2 and argv[1] in ("-h", "--help") and argv[0] in COMMANDS:
        canonical, handler = COMMANDS[argv[0]]
        if canonical == "search":
            print_help()
            return 0
        handler(None, db_path, argv[1:])
        return 0

    conn = connect(db_path)
    try:
        name = argv[0]
        if name not in COMMANDS:
            cmd_search(conn, db_path, argv)
            return 0
        _, handler = COMMANDS[name]
        handler(conn, db_path, argv[1:])
        return 0
    finally:
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as exc:
            LOGGER.warning("CLI DB checkpoint failed: %s", exc)
        conn.close()
        LOGGER.info("CLI DB closed %s", db_path)
