from __future__ import annotations

import sys

from core.constants import AmbiguousTarget, UserError
from core.config import LOGGER, log_path, parse_global_options
from db import candidate_lines, connect
from .dispatch import dispatch


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        db_path, quiet, rest = parse_global_options(effective_argv)
        return dispatch(db_path, quiet, rest)
    except AmbiguousTarget as exc:
        LOGGER.warning("Ambiguous target: %s", exc.target)
        try:
            db_path, _, _ = parse_global_options(effective_argv)
            conn = connect(db_path)
            try:
                print(f"ambiguous target: {exc.target}", file=sys.stderr)
                print("use #id, URL, or platform:platform_id:", file=sys.stderr)
                for line in candidate_lines(conn, exc.creator_ids):
                    print(line, file=sys.stderr)
            finally:
                conn.close()
        except Exception:
            print(str(exc), file=sys.stderr)
        return 2
    except UserError as exc:
        LOGGER.warning("User error: %s", exc)
        message = str(exc)
        if message:
            print(f"error: {message}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        LOGGER.info("Interrupted by user")
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        from core.config import log_exception
        log_exception("Fatal error", exc)
        print(f"error: unexpected failure, see {log_path().name}", file=sys.stderr)
        return 1
