from __future__ import annotations

APP = "clog"
DB_DIR_NAME = "db"
DB_NAME = "clog.sqlite"
CONFIG_NAME = "clog.json"
LOG_NAME = "clog.log"
SCHEMA_VERSION = 3


class UserError(Exception):
    pass


class ConflictError(UserError):
    pass


class AmbiguousTarget(UserError):
    def __init__(self, target: str, creator_ids: list[int]):
        super().__init__(f"Target is ambiguous: {target}")
        self.target = target
        self.creator_ids = creator_ids
