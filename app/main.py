"""
FastAPI UI layer. Talks only to app/analyst.ask() (the planner agent) and
app/sql_connector.py (for the read-only /schema endpoint) — it has no
database or agent logic of its own.

AUTH NOTE: there is deliberately no authentication or multi-tenancy here.
This is a single-user local learning project; every request is trusted.
Do not deploy this behind a public URL as-is — adding auth (e.g. an
API key header or OAuth) is a separate, deferred concern.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.analyst import ask
from app.sql_connector import get_table_schema, list_tables

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Hotel Data Analyst")

# Serves style.css / app.js under /static/*; index.html is served
# separately at "/" below (StaticFiles alone won't serve "/" -> index.html
# without directory-listing quirks, so we handle that route explicitly).
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    sql: list[str]
    agents_used: list[str]
    elapsed_seconds: float


@app.get("/")
def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    """Liveness/readiness check. Confirms the API process is up and can
    reach Postgres — does NOT check the OpenAI API (that's only exercised
    on /ask, since checking it here would cost a request on every poll)."""
    try:
        list_tables()
        db_ok = True
    except Exception:
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

    # TODO(guardrails): an input rail belongs right here, before `ask()` is
    # called — see AGENT_PLAN.md section 2. It would validate/reject the
    # raw question (off-topic, prompt-injection, etc.) before any agent
    # sees it. Not implemented in this pass.

    result = await ask(request.question)

    # TODO(guardrails): an output rail belongs right here, before the
    # response is returned to the client — see AGENT_PLAN.md section 2.

    return AskResponse(
        answer=result.answer,
        sql=result.sql,
        agents_used=result.agents_used,
        elapsed_seconds=result.elapsed_seconds,
    )
