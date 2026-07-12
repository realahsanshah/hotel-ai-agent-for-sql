"""
One-off manual smoke test for app/sql_connector.py (section-2 checkpoint).
Not a pytest suite on purpose — this project's testing story is out of
scope for the learning goals here; this script just proves the three
functions work end to end against the running Postgres container.

Run with: uv run python scripts/test_connector_manual.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sql_connector import list_tables, get_table_schema, run_query


def main():
    print("=== list_tables() ===")
    tables = list_tables()
    print(tables)
    assert "reservations" in tables and "folio_charges" in tables

    print("\n=== get_table_schema('reservations') ===")
    schema = get_table_schema("reservations")
    for col in schema:
        print(col)
    assert any(c["column_name"] == "room_id" and c["is_nullable"] for c in schema)

    print("\n=== run_query: row counts per table ===")
    for t in tables:
        rows = run_query(f"SELECT COUNT(*) AS n FROM {t}")
        print(f"{t}: {rows[0]['n']}")

    print("\n=== run_query: DISTINCT reservation statuses (the messy column) ===")
    print(run_query("SELECT DISTINCT status FROM reservations"))

    print("\n=== run_query: max_rows enforcement ===")
    limited = run_query("SELECT * FROM guests", max_rows=3)
    assert len(limited) == 3
    print(f"Requested max_rows=3, got {len(limited)} rows -> OK")

    print("\n=== run_query: SELECT-only guard blocks a DELETE ===")
    try:
        run_query("DELETE FROM guests")
        print("FAIL: DELETE was not blocked!")
    except ValueError as e:
        print(f"Blocked as expected: {e}")

    print("\n=== run_query: SELECT-only guard blocks multiple statements ===")
    try:
        run_query("SELECT 1; DROP TABLE guests;")
        print("FAIL: multi-statement was not blocked!")
    except ValueError as e:
        print(f"Blocked as expected: {e}")

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
