"""
Agent-callable tools. Thin wrappers around app/sql_connector.py — the
wrapping exists to (a) give the LLM a name + docstring it can reason about
when deciding what to call, and (b) record ground-truth facts (SQL run,
which agent ran it) into the shared HotelAnalystContext as a side effect,
independent of anything the LLM says in its own output.

Only SQLAgent is given these tools (see app/agents.py) — PlannerAgent and
SummarizerAgent never touch the database directly.
"""

from agents import RunContextWrapper, function_tool

from app.context import HotelAnalystContext
from app.sql_connector import get_table_schema, list_tables, run_query


@function_tool
def list_tables_tool(ctx: RunContextWrapper[HotelAnalystContext]) -> list[str]:
    """List every table in the hotel database. Always call this first if
    you don't already know the schema from earlier in this conversation —
    never guess a table name."""
    ctx.context.record_agent("SQLAgent")
    return list_tables()


@function_tool
def get_table_schema_tool(
    ctx: RunContextWrapper[HotelAnalystContext], table_name: str
) -> list[dict]:
    """Get the column names, data types, and nullability for one table.
    Call this before referencing a table's columns in SQL — never guess a
    column name."""
    ctx.context.record_agent("SQLAgent")
    return get_table_schema(table_name)


@function_tool
def run_query_tool(
    ctx: RunContextWrapper[HotelAnalystContext], sql: str, max_rows: int = 50
) -> list[dict]:
    """Run a single read-only SQL SELECT query against the hotel database
    and return up to max_rows rows.

    IMPORTANT: this dataset has intentionally inconsistent casing/format on
    every status-like column (e.g. rooms.room_status, reservations.status,
    payments.method, housekeeping_tasks.status). Before filtering on any
    such column, run `SELECT DISTINCT <column> FROM <table>` first to see
    the actual values in use — do not assume a clean enum like 'Cancelled'
    is the only spelling; also check for variants like 'cancelled' or
    'CANCELLED'.

    Only SELECT (or WITH ... SELECT) statements are accepted; INSERT,
    UPDATE, DELETE, DDL, and multi-statement queries are rejected."""
    ctx.context.record_agent("SQLAgent")
    ctx.context.record_sql(sql)
    rows = run_query(sql, max_rows=max_rows)
    ctx.context.record_evidence(sql, rows)
    return rows
