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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.analyst import ask
from app.sql_connector import get_table_schema, list_tables

app = FastAPI(title="Hotel Data Analyst")

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


class AskRequest(BaseModel):
    question: str


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
