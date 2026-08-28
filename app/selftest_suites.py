"""Suites registered by phases 1 and up.

Everything heavy is imported inside the suite function, so importing this module stays
free and safe in every environment.
"""

from __future__ import annotations

import argparse

from app.selftest import Check, suite


@suite("config")
def _config(args: argparse.Namespace) -> list[Check]:
    from app.config import SECRET_KEYS, Config

    cfg = Config.load()
    dump = cfg.redacted_dump()
    text = repr(dump)
    leaked = [k for k in SECRET_KEYS if k.split(".")[-1] in text and "***" not in text]
    warnings = cfg.warnings()
    return [
        Check("config_loads", True, f"{cfg.source_file}", {"profile": cfg.profile}),
        Check("config_redacts_secrets", not leaked, ", ".join(leaked) or "no secrets in dump"),
        Check(
            "config_warnings",
            True,
            "; ".join(warnings) or "none",
            {"warnings": len(warnings)},
        ),
    ]


@suite("db")
def _db(args: argparse.Namespace) -> list[Check]:
    from app.config import Config
    from app.db import migrate as migrations
    from app.db.dao import Dao, capabilities, connect

    cfg = Config.load()
    conn = connect(fts=cfg.get("db.fts", "auto") != "off")
    caps = capabilities(conn)
    version = migrations.current_version(conn)
    dao = Dao(conn)
    count = len(dao.list_meetings(limit=1))
    checks = [
        Check(
            "db_migrated",
            version == max(m.version for m in migrations.discover()),
            f"schema_version={version}",
            {"schema_version": version},
        ),
        Check(
            "fts5",
            True,
            "FTS5 available" if caps.fts else "FTS5 missing — search falls back to LIKE",
            {"fts5": caps.fts},
        ),
        Check("db_readable", True, f"{count} meeting(s) visible"),
    ]
    conn.close()
    return checks
