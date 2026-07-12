"""
Top-level entry point for asking the hotel analyst a question. This is the
only function app/main.py (the FastAPI layer) and test scripts should call
— everything else in app/agents.py, app/tools.py, and app/context.py is an
implementation detail behind it.
"""

import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from agents import RunConfig, Runner
from agents.memory.sqlite_session import SQLiteSession

from app.agents import planner_agent
from app.context import HotelAnalystContext

# Conversation history lives here so follow-up questions ("when was it?")
# can refer back to earlier turns. SQLite (stdlib, no extra service) is
# enough for a single-user local project — see README for how session_id
# is generated/passed by the frontend. File-based (not ':memory:') so
# history survives an API restart; gitignored since it's runtime state.
SESSIONS_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sessions.db"
SESSIONS_DB_PATH.parent.mkdir(exist_ok=True)


@dataclass
class AnalystResponse:
    answer: str
    sql: list[str]
    agents_used: list[str]
    elapsed_seconds: float
    # SQL + result rows, formatted as text. Not part of the public API
    # response (see app/main.py's AskResponse) — only used internally to
    # feed the output guardrail's fact-check (app/guardrails.py).
    evidence: list[str]


async def get_recent_history_text(session_id: str, limit: int = 6) -> str:
    """Renders the last `limit` conversation items as plain text.

    Used by app/main.py to give the input guardrail (app/guardrails.py)
    enough context to judge short follow-ups correctly — e.g. "list of
    it?" reads as gibberish/off-topic in isolation, which is exactly what
    happened before this existed: the input rail blocked a legitimate
    follow-up because self_check_input only ever sees the current message,
    with no idea what "it" refers to."""
    session = SQLiteSession(session_id, db_path=SESSIONS_DB_PATH)
    items = await session.get_items(limit=limit)
    lines = []
    for item in items:
        role = item.get("role")
        if role == "user":
            lines.append(f"User: {item.get('content')}")
        elif role == "assistant":
            content = item.get("content")
            if isinstance(content, list):
                text = " ".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            else:
                text = content
            if text:
                lines.append(f"Assistant: {text}")
    return "\n".join(lines)


async def ask(question: str, session_id: str | None = None) -> AnalystResponse:
    """Runs the planner -> SQL agent -> summarizer pipeline for one
    question and returns the answer plus the ground-truth SQL/agent trail
    recorded via the shared context (see app/context.py for why we trust
    that over the LLM's own narration).

    session_id: when provided, prior turns for this session are loaded as
    context and this turn is appended afterwards — this is what makes
    follow-up questions like "when was it?" resolvable. When omitted (e.g.
    from the manual test scripts), each call is a fresh, history-free run."""
    context = HotelAnalystContext()

    session = SQLiteSession(session_id, db_path=SESSIONS_DB_PATH) if session_id else None

    # workflow_name groups every trace from this project under one name in
    # the OpenAI traces dashboard; a fresh group_id per call keeps each
    # question's trace separately identifiable (see AGENT_PLAN.md section 3).
    run_config = RunConfig(
        workflow_name="hotel-ask",
        group_id=str(uuid.uuid4()),
    )

    start = time.monotonic()
    result = await Runner.run(
        planner_agent,
        question,
        context=context,
        run_config=run_config,
        session=session,
    )
    elapsed = time.monotonic() - start

    context.record_agent("PlannerAgent")

    return AnalystResponse(
        answer=str(result.final_output),
        sql=context.sql_log,
        agents_used=context.agents_used,
        elapsed_seconds=elapsed,
        evidence=context.evidence_log,
    )
