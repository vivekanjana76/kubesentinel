"""
Run the Supabase schema migration for the RAG runbooks table.

supabase-py cannot execute DDL (it uses PostgREST, not a direct Postgres
connection). This script uses psycopg2 with DATABASE_URL for DDL execution.

Usage:
    py -3.12 -m agent.rag.migrate
"""

import pathlib
import sys

import psycopg2
import structlog

from agent.rag.settings import settings

log = structlog.get_logger()

SQL_FILE = pathlib.Path(__file__).parent / "migrations" / "001_create_runbooks.sql"


def run_migration() -> None:
    if not settings.database_url:
        log.error(
            "migrate.missing_database_url",
            hint="Set DATABASE_URL in .env — see .env.example for the format.",
        )
        sys.exit(1)

    sql = SQL_FILE.read_text(encoding="utf-8")
    log.info("migrate.start", sql_file=str(SQL_FILE))

    conn = psycopg2.connect(settings.database_url)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)
        log.info("migrate.success", message="Schema is up to date.")
    except psycopg2.Error as exc:
        log.error("migrate.failed", error=str(exc))
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
