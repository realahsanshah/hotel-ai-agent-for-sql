"""
Agent definitions and composition — implements the architecture decided in
AGENT_PLAN.md: PlannerAgent (orchestrator) -> SQLAgent (as a tool) and
SummarizerAgent (as a tool), using the agents-as-tools pattern rather than
handoffs, because the planner needs each sub-agent's result back in hand
to keep working (see AGENT_PLAN.md section 1 for the full justification).

Only PlannerAgent is ever run directly (from app/main.py or a test
script). SQLAgent and SummarizerAgent are only reachable through it, via
the `query_hotel_database` and `summarize_results` tools below.
"""

import os

from agents import Agent, ItemHelpers, RunResult

from app.context import HotelAnalystContext
from app.tools import get_table_schema_tool, list_tables_tool, run_query_tool

# One model for every agent in this project — small enough to keep costs
# down for a learning project, capable enough to reliably call tools in
# sequence. Override via .env if you want to compare model quality.
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")


# ---------------------------------------------------------------------------
# SQLAgent — the only agent with database tools.
# ---------------------------------------------------------------------------

SQL_AGENT_INSTRUCTIONS = """\
You are a SQL analyst for a hotel Property Management System (PMS) database.

Rules you must always follow:
1. Never assume you know the schema. Call `list_tables_tool` and
   `get_table_schema_tool` to confirm table/column names before writing any
   SQL that references them, unless you have already confirmed them earlier
   in this same conversation.
2. This dataset has intentionally inconsistent casing/format on every
   status-like column (room status, reservation status, payment method,
   housekeeping status). Before filtering on such a column with a WHERE
   clause, run `SELECT DISTINCT <column> FROM <table>` to discover the
   actual set of values — do not assume there is only one spelling of e.g.
   "cancelled". When filtering, match ALL casing variants you found (e.g.
   with `LOWER(column) = 'cancelled'` or an explicit IN list).
3. There is no precomputed total on folios anywhere. Always compute a
   folio's total by `SUM`ming `folio_charges.amount` for that folio_id —
   never assume a cached total column exists.
4. Only SELECT/WITH queries are possible; the query tool rejects anything
   else. If you need to combine data from multiple tables, use JOINs
   within a single query rather than trying to run separate write
   operations.
5. Once you have the data you need, respond with a concise, factual
   summary of the query result (the caller will further polish the
   wording) — do not fabricate numbers that didn't come from a query.
"""

sql_agent = Agent[HotelAnalystContext](
    name="SQLAgent",
    instructions=SQL_AGENT_INSTRUCTIONS,
    model=MODEL_NAME,
    tools=[list_tables_tool, get_table_schema_tool, run_query_tool],
)


# ---------------------------------------------------------------------------
# SummarizerAgent — no database tools, just turns raw findings into prose.
# ---------------------------------------------------------------------------

SUMMARIZER_INSTRUCTIONS = """\
You turn hotel-data query results into a clear, concise answer for a hotel
operations user. You will be given the original question and the raw
findings from a SQL query. Write a short, direct natural-language answer
that a hotel manager could read in a few seconds. Cite concrete numbers
from the findings. Do not invent any figure that isn't present in the
findings you were given.
"""

summarizer_agent = Agent[HotelAnalystContext](
    name="SummarizerAgent",
    instructions=SUMMARIZER_INSTRUCTIONS,
    model=MODEL_NAME,
)


# ---------------------------------------------------------------------------
# Wrap both sub-agents as tools. `custom_output_extractor` records which
# agent actually ran into the shared context (see app/context.py) — we
# don't rely on the LLM's own text to say "I used SQLAgent", we record it
# as a side effect of the tool actually being invoked, and reproduce the
# SDK's default extraction behavior (last assistant message) ourselves so
# call sites see the same output shape they'd get without a custom
# extractor.
# ---------------------------------------------------------------------------


async def _record_sql_agent(run_result: RunResult) -> str:
    run_result.context_wrapper.context.record_agent("SQLAgent")
    return ItemHelpers.text_message_outputs(run_result.new_items)


async def _record_summarizer_agent(run_result: RunResult) -> str:
    run_result.context_wrapper.context.record_agent("SummarizerAgent")
    return ItemHelpers.text_message_outputs(run_result.new_items)


query_hotel_database_tool = sql_agent.as_tool(
    tool_name="query_hotel_database",
    tool_description=(
        "Ask a data question that requires exploring the hotel database "
        "schema and/or running SQL against it. Pass the specific question "
        "you need answered (not the user's raw question verbatim if you've "
        "already narrowed it down). Returns the SQL agent's findings as "
        "text. Can be called more than once, e.g. first to discover "
        "DISTINCT values on a status column, then again with a refined "
        "query."
    ),
    custom_output_extractor=_record_sql_agent,
)

summarize_results_tool = summarizer_agent.as_tool(
    tool_name="summarize_results",
    tool_description=(
        "Turn raw query findings into a final, polished natural-language "
        "answer. Pass the original user question together with the "
        "findings returned by query_hotel_database. Call this once you "
        "have all the data you need, right before producing your final "
        "answer."
    ),
    custom_output_extractor=_record_summarizer_agent,
)


# ---------------------------------------------------------------------------
# PlannerAgent — the only agent the FastAPI layer talks to directly.
# ---------------------------------------------------------------------------

PLANNER_INSTRUCTIONS = """\
You are a hotel data analyst assistant. You never query the database
yourself — you decide what data is needed to answer the user's question
and delegate to `query_hotel_database`, possibly multiple times if the
first result reveals you need to check something else (e.g. distinct
status values, or a follow-up aggregate). Once you have enough findings,
call `summarize_results` with the original question and your findings to
produce the final answer, then return that answer as your final response.

If the question cannot be answered from the hotel database (e.g. it's
off-topic), say so directly instead of calling any tool.
"""

planner_agent = Agent[HotelAnalystContext](
    name="PlannerAgent",
    instructions=PLANNER_INSTRUCTIONS,
    model=MODEL_NAME,
    tools=[query_hotel_database_tool, summarize_results_tool],
)
