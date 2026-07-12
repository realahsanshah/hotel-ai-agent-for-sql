"""
Connector layer: the ONLY place in this project that talks to Postgres via
raw SQL/psycopg. Kept separate from agent code (app/agents.py, section 4) so
that:
  1. The agent's "tools" are thin wrappers around these functions — the
     agent never constructs a psycopg connection itself.
  2. This module is independently testable (see the manual test script
     used at the section-2 checkpoint) without needing the OpenAI Agents
     SDK or an API key.

SECURITY NOTE (read this before trusting the guard below):
------------------------------------------------------------
`run_query`'s SELECT-only check is a guardrail against *accidents* (the LLM
emitting an UPDATE/DELETE by mistake), NOT a real security boundary. A
well-crafted SELECT can still read data it shouldn't be able to see, chain
into a DoS via an expensive query, or exfiltrate data through timing/error
side-channels — none of that is stopped here. Real protection (least-
privilege DB role, row-level security, query cost limits, and semantic
input/output filtering) is a separate phase I'm doing by hand with NeMo
Guardrails later. Do not treat this module as sufficient for a
multi-tenant or internet-facing deployment.
"""

import os
import re

import psycopg
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": os.getenv("POSTGRES_PORT", "5432"),
    "dbname": os.getenv("POSTGRES_DB", "hotel_pms"),
    "user": os.getenv("POSTGRES_USER", "hotel_admin"),
    "password": os.getenv("POSTGRES_PASSWORD", "hotel_dev_password"),
}

# Keywords that must never appear as a statement-starting or standalone
# command in agent-issued SQL. This is a blocklist, which is inherently
# incomplete (see module docstring) — it exists to catch the common/obvious
# cases, not to be exhaustive.
_FORBIDDEN_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE",
    "GRANT", "REVOKE", "ATTACH", "DETACH", "PRAGMA", "VACUUM", "COPY",
    "CALL", "DO", "EXECUTE", "MERGE",
]


def _get_connection() -> psycopg.Connection:
    return psycopg.connect(**DB_CONFIG)


def _assert_select_only(sql: str) -> None:
    """Raises ValueError if `sql` is anything other than a single SELECT
    statement. Deliberately conservative: reject on ambiguity rather than
    try to be clever about parsing SQL with regex."""
    stripped = sql.strip()
    if not stripped:
        raise ValueError("Empty query.")

    # Block multiple statements. A single trailing semicolon is fine; a
    # semicolon followed by more (non-whitespace) content is not.
    without_trailing_semi = stripped.rstrip(";").rstrip()
    if ";" in without_trailing_semi:
        raise ValueError("Multiple statements are not allowed.")

    # Must start with SELECT or WITH (CTE feeding a SELECT). Comparing the
    # first token avoids false positives on e.g. a column literally named
    # "update_count" appearing later in a legitimate SELECT.
    first_word_match = re.match(r"^\s*([A-Za-z]+)", without_trailing_semi)
    first_word = first_word_match.group(1).upper() if first_word_match else ""
    if first_word not in ("SELECT", "WITH"):
        raise ValueError(
            f"Only SELECT queries are allowed (statement starts with '{first_word}')."
        )

    # Reject forbidden keywords anywhere in the query as whole words. This
    # also catches e.g. `WITH x AS (DELETE ...)` smuggling attempts, and
    # `SELECT ... ; DROP TABLE ...` (belt-and-suspenders with the semicolon
    # check above).
    upper_sql = without_trailing_semi.upper()
    for keyword in _FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", upper_sql):
            raise ValueError(f"Forbidden keyword '{keyword}' found in query.")


def list_tables() -> list[str]:
    """Returns all user table names in the public schema. This is the
    agent's entry point for schema discovery — it must call this before
    guessing at table names."""
    query = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(query)
        return [row[0] for row in cur.fetchall()]


def get_table_schema(table_name: str) -> list[dict]:
    """Returns column metadata for one table: name, data type, nullability.
    Queried from information_schema rather than hardcoded so it can never
    drift from the real schema in db/schema.sql."""
    query = """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(query, (table_name,))
        rows = cur.fetchall()
        if not rows:
            raise ValueError(f"Table '{table_name}' not found.")
        return [
            {"column_name": r[0], "data_type": r[1], "is_nullable": r[2] == "YES"}
            for r in rows
        ]


def run_query(sql: str, max_rows: int = 50) -> list[dict]:
    """Runs a read-only SQL query and returns up to `max_rows` rows as
    dicts. `max_rows` is enforced by wrapping the query rather than trusting
    a LIMIT the caller may or may not have included."""
    _assert_select_only(sql)

    without_trailing_semi = sql.strip().rstrip(";")
    wrapped_sql = f"SELECT * FROM ({without_trailing_semi}) AS _subquery LIMIT %s"

    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(wrapped_sql, (max_rows,))
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
