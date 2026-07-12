"""
Top-level entry point for asking the hotel analyst a question. This is the
only function app/main.py (the FastAPI layer) and test scripts should call
— everything else in app/agents.py, app/tools.py, and app/context.py is an
implementation detail behind it.
"""

import time
import uuid
from dataclasses import dataclass

from agents import RunConfig, Runner

from app.agents import planner_agent
from app.context import HotelAnalystContext


@dataclass
class AnalystResponse:
    answer: str
    sql: list[str]
    agents_used: list[str]
    elapsed_seconds: float


async def ask(question: str) -> AnalystResponse:
    """Runs the planner -> SQL agent -> summarizer pipeline for one
    question and returns the answer plus the ground-truth SQL/agent trail
    recorded via the shared context (see app/context.py for why we trust
    that over the LLM's own narration)."""
    context = HotelAnalystContext()

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
    )
    elapsed = time.monotonic() - start

    context.record_agent("PlannerAgent")

    return AnalystResponse(
        answer=str(result.final_output),
        sql=context.sql_log,
        agents_used=context.agents_used,
        elapsed_seconds=elapsed,
    )
