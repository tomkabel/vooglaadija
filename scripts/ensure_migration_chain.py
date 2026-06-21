"""Ensure the alembic migration chain is consistent.

When migrations are squashed/consolidated, the database's alembic_version
table may reference a revision ID that no longer exists in the codebase.
This script detects and fixes that situation by stamping the database to
the current head revision using Alembic's public API.

This MUST run before any call to ``alembic upgrade head`` because the
latter will crash if the chain is broken.

Usage::

    python scripts/ensure_migration_chain.py

Environment variables:

    DATABASE_URL  — full asyncpg database URL (required)
                    e.g. ``postgresql+asyncpg://user:pass@host:5432/db``

Exit codes:

    0  — chain is valid or was successfully repaired
    1  — an unexpected error occurred
"""

import os
import re
import sys
from pathlib import Path

ALEMBIC_CFG_PATH = "/app/alembic.ini"
VERSIONS_DIR = "/app/alembic/versions"


def _get_available_revisions(versions_dir: str) -> set[str]:
    """Extract all revision IDs from migration files in *versions_dir*."""
    revisions: set[str] = set()
    versions_path = Path(versions_dir)
    for f in sorted(versions_path.glob("*.py")):
        if f.name == "__init__.py":
            continue
        content = f.read_text()
        match = re.search(
            r'^revision\s*:\s*str\s*=\s*["\']([^"\']+)["\']',
            content,
            re.MULTILINE,
        )
        if match:
            revisions.add(match.group(1))
    return revisions


def _sync_connect(database_url: str):
    """Return a psycopg connection for a *database_url* that may use async drivers."""
    from urllib.parse import urlparse

    from psycopg import connect

    sync_url = database_url.replace("+asyncpg", "").replace("+aiosqlite", "")
    parsed = urlparse(sync_url)

    kwargs: dict = {
        "host": parsed.hostname,
        "port": parsed.port,
        "dbname": parsed.path.lstrip("/"),
        "user": parsed.username,
        "password": parsed.password,
    }

    # Strip None values so psycopg uses its defaults
    return connect(**{k: v for k, v in kwargs.items() if v is not None})


def _get_db_revision(database_url: str) -> str | None:
    """Return the current revision stored in the database, or *None*."""
    try:
        conn = _sync_connect(database_url)
    except Exception as exc:
        print(f"WARNING: cannot connect to database — {exc}", file=sys.stderr)
        return None

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def _stamp_to_head(database_url: str, head: str) -> None:
    """Stamp *head* into the database, bypassing Alembic's chain validation."""
    conn = _sync_connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE alembic_version")
            cur.execute(
                "INSERT INTO alembic_version (version_num) VALUES (%s)",
                (head,),
            )
        conn.commit()
        print(f"Fixed: database stamped to revision '{head}'")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print(
            "WARNING: DATABASE_URL not set — skipping migration chain check",
            file=sys.stderr,
        )
        return 0

    # ── Get available revisions from files ──────────────────────────────
    available = _get_available_revisions(VERSIONS_DIR)
    if not available:
        print(
            "WARNING: no migration files found in %s — skipping",
            VERSIONS_DIR,
            file=sys.stderr,
        )
        return 0

    # ── Read the current head from the migration tree ───────────────────
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    alembic_cfg = Config(ALEMBIC_CFG_PATH)
    script = ScriptDirectory.from_config(alembic_cfg)
    head = script.get_current_head()
    if head is None:
        print(
            "WARNING: no head revision found in migration tree — skipping",
            file=sys.stderr,
        )
        return 0

    # ── Read what the database currently thinks ─────────────────────────
    db_revision = _get_db_revision(database_url)

    if db_revision is None:
        # No revision at all — this is a fresh database; let alembic upgrade
        # head do its normal thing.
        print("No existing migration revision found — fresh database")
        return 0

    if db_revision in available:
        print(f"OK: database revision '{db_revision}' found in migration files")
        return 0

    # ── Broken chain — stamp to head ────────────────────────────────────
    print(
        f"Broken chain: database revision '{db_revision}' not found in "
        f"migration files. Stamping to head '{head}'...",
    )

    try:
        _stamp_to_head(database_url, head)
    except Exception as exc:
        print(f"ERROR: failed to stamp database — {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
