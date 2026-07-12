"""
FastAPI backend. Talks only to app/analyst.ask() (the planner agent) and
app/sql_connector.py (for the read-only /schema endpoint) — it has no
database or agent logic of its own.

The UI (web/, a small Next.js app) runs as a separate process on its own
dev-server port and calls this API over HTTP, so CORS is enabled below for
local development. This is a plain JSON API with no bundled frontend of
its own.

AUTH NOTE: there is deliberately no authentication or multi-tenancy here.
This is a single-user local learning project; every request is trusted.
Do not deploy this behind a public URL as-is — adding auth (e.g. an
API key header or OAuth) is a separate, deferred concern.
"""

import logging
import time
from contextlib import asynccontextmanager

from app.logging_config import setup_logging

# Must run before any other app module logs anything, so import order here
# matters — this is the first app import in the process.
setup_logging()

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.analyst import ask  # noqa: E402
from app.sql_connector import get_table_schema, list_tables  # noqa: E402

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Hotel Data Analyst API starting up.")
    yield
    logger.info("Hotel Data Analyst API shutting down.")


app = FastAPI(title="Hotel Data Analyst", lifespan=lifespan)

# Next.js dev server runs on :3000 by default; this API runs on :8000.
# Different ports = different origins, so the browser blocks the fetch()
# calls in web/ without explicit CORS headers. Wide open ("*" methods/
# headers) is fine for a local single-user dev setup — see AUTH NOTE above.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Logs every request that hits the API (method, path, status, timing),
    not just /ask — so you have a full record of e.g. the frontend's
    /health polling and /schema fetches too, in logs/app.log."""
    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.exception(
            "%s %s raised an unhandled exception after %.1fms",
            request.method,
            request.url.path,
            elapsed_ms,
        )
        raise
    elapsed_ms = (time.monotonic() - start) * 1000
    logger.info(
        "%s %s -> %d (%.1fms)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


class AskRequest(BaseModel):
    question: str
    # Generated client-side (one per browser tab/chat) and echoed back on
    # every call so follow-up questions ("when was it?") can be resolved
    # against that session's prior turns. Omit it for a stateless, one-off
    # question. See app/analyst.py for how this maps to conversation history.
    session_id: str | None = None


class AskResponse(BaseModel):
    answer: str
    sql: list[str]
    agents_used: list[str]
    elapsed_seconds: float


@app.get("/health")
def health():
    """Liveness/readiness check. Confirms the API process is up and can
    reach Postgres — does NOT check the OpenAI API (that's only exercised
    on /ask, since checking it here would cost a request on every poll)."""
    try:
        list_tables()
        db_ok = True
    except Exception:
        logger.exception("Health check failed to reach Postgres.")
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok}


@app.get("/schema")
def schema():
    """Returns every table and its columns — lets the UI show the user
    what data is available without them having to ask the agent first."""
    tables = list_tables()
    return {table: get_table_schema(table) for table in tables}


@app.post("/ask", response_model=AskResponse)
async def ask_question(request: AskRequest):
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question must not be empty.")

    logger.info(
        "Question received (session=%s): %r",
        request.session_id or "-",
        request.question,
    )

    # TODO(guardrails): an input rail belongs right here, before `ask()` is
    # called — see AGENT_PLAN.md section 2. It would validate/reject the
    # raw question (off-topic, prompt-injection, etc.) before any agent
    # sees it. Not implemented in this pass.

    try:
        result = await ask(request.question, session_id=request.session_id)
    except Exception:
        logger.exception(
            "Agent pipeline failed for question (session=%s): %r",
            request.session_id or "-",
            request.question,
        )
        raise

    # TODO(guardrails): an output rail belongs right here, before the
    # response is returned to the client — see AGENT_PLAN.md section 2.

    logger.info(
        "Answered in %.2fs via %s (session=%s): %r",
        result.elapsed_seconds,
        " -> ".join(result.agents_used),
        request.session_id or "-",
        result.answer,
    )
    for sql in result.sql:
        logger.info("SQL run: %s", " ".join(sql.split()))

    return AskResponse(
        answer=result.answer,
        sql=result.sql,
        agents_used=result.agents_used,
        elapsed_seconds=result.elapsed_seconds,
    )
