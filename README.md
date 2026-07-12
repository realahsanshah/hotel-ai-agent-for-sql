# Hotel Data Analyst

A Postgres-backed, multi-agent hotel data analyst, built as a learning
project for the OpenAI Agents SDK. Ask questions in plain English about a
synthetic hotel PMS (property management system) dataset and get back an
answer, the SQL that produced it, and which agent(s) handled the request.

See [AGENT_PLAN.md](AGENT_PLAN.md) for the full reasoning behind the agent
architecture (single vs. multi-agent, handoffs vs. agents-as-tools, and
tracing).

## Architecture

```
FastAPI (app/main.py)
  -> check_input()                 (app/guardrails.py, NeMo Guardrails input rail)
  -> PlannerAgent                  (app/agents.py)
       -> query_hotel_database     tool -> SQLAgent
                                             -> list_tables / get_table_schema / run_query
                                                (app/tools.py -> app/sql_connector.py -> Postgres)
       -> summarize_results        tool -> SummarizerAgent
  -> check_output()                (app/guardrails.py, NeMo Guardrails output rail)
```

- **db/schema.sql** — plain SQL schema for the hotel PMS dataset (no ORM).
  Several columns (room status, reservation status, payment method,
  housekeeping status) use *intentionally inconsistent casing*
  (`'Clean'` / `'dirty'` / `'Out_Of_Order'`) — this is deliberate messy
  data, not a bug, so the agent has to discover real values via
  `SELECT DISTINCT` rather than assume a clean enum.
- **ingest.py** — generates and loads a reproducible (seed=42) synthetic
  dataset: 3 properties, ~120 rooms, 350 guests, 400 reservations, and
  their folios/charges/payments/housekeeping tasks. Idempotent via
  drop-and-recreate (not upsert) — see the module docstring for why.
- **app/sql_connector.py** — the only module that talks to Postgres.
  `list_tables()`, `get_table_schema()`, `run_query()`. `run_query`
  enforces a SELECT-only guard (blocks INSERT/UPDATE/DELETE/DDL and
  multi-statement queries). This guard is an accident-prevention
  mechanism, **not** a real security boundary — see the module docstring.
- **app/agents.py / app/tools.py / app/context.py / app/analyst.py** —
  the agent pipeline. `PlannerAgent` never touches the database directly;
  it delegates to `SQLAgent` (via the agents-as-tools pattern) for schema
  exploration and querying, and to `SummarizerAgent` for the final
  natural-language answer. The SQL actually run and which agents were
  involved are recorded as a side effect in a shared context object
  (`app/context.py`), not inferred from the LLM's own narration.
- **app/main.py** — a plain FastAPI JSON API (`/ask`, `/health`, `/schema`)
  with no bundled frontend. No auth (single local user, deliberately
  deferred — see the module docstring). Every request is logged (see
  `app/logging_config.py`) to console + `logs/app.log`.
- **web/** — a small Next.js chat UI that calls the API above. Runs as a
  separate process on its own dev server; `app/main.py` enables CORS for
  it (see that module's docstring).
- **guardrails/ + app/guardrails.py** — NeMo Guardrails input/output rails.
  The input rail blocks off-topic questions and prompt-injection attempts
  before they reach any agent; the output rail blocks unsafe responses
  AND fact-checks the final answer's claims against the real SQL results
  collected during the run (`app/context.py`'s `evidence_log`). This is
  what catches summarizer hallucinations — e.g. it caught the pipeline
  once claiming "11 properties" when the query it ran actually returned 3.
  Both rails run in their own `LLMRails` instance, separate from the
  OpenAI Agents SDK pipeline — see the module docstring for how "blocked
  vs. allowed" is detected.
- **Conversation memory** — `/ask` accepts an optional `session_id`;
  when present, prior turns are loaded via the Agents SDK's `SQLiteSession`
  (`app/analyst.py`, stored in `data/sessions.db`) so follow-up questions
  like "how was it distributed?" resolve against earlier turns. The
  Next.js UI generates one `session_id` per page load.

## Prerequisites

- [Docker](https://www.docker.com/) (for Postgres)
- [uv](https://docs.astral.sh/uv/) (Python package/dependency manager)
- Node.js (for the `web/` frontend)
- An OpenAI API key

## Setup

1. Copy the env file and fill in your OpenAI API key:

   ```bash
   cp .env.example .env
   # edit .env and set OPENAI_API_KEY=sk-...
   ```

   `POSTGRES_PORT` defaults to `5433` (not the standard `5432`) to avoid
   clashing with any other local Postgres instance — change it in `.env`
   if you need to.

2. Start Postgres:

   ```bash
   docker-compose up -d
   ```

3. Install dependencies and load the seed data:

   ```bash
   uv sync
   uv run python ingest.py
   ```

   This drops and recreates the schema every time it's run — see
   `ingest.py`'s docstring for why that's the right call for this
   synthetic, reproducible dataset.

4. Run the API:

   ```bash
   uv run uvicorn app.main:app --reload
   ```

5. In a separate terminal, run the web UI:

   ```bash
   cd web
   npm install
   npm run dev
   ```

6. Open [http://localhost:3000](http://localhost:3000) and ask a question,
   e.g. *"Which reservation statuses exist, and how many of each?"* or
   *"Which folios are underpaid relative to their charges?"*

## Manual test scripts

These aren't a pytest suite (out of scope for this project) — they're
one-off smoke tests used at the section checkpoints during development:

```bash
uv run python scripts/test_connector_manual.py   # sql_connector.py against a running Postgres
uv run python scripts/test_agent_manual.py        # full agent pipeline, requires a real OPENAI_API_KEY
```

## What's deliberately out of scope

- **Auth / multi-tenancy** — single local user only (see `app/main.py`).
- **Real security boundary on SQL execution** — the SELECT-only guard in
  `app/sql_connector.py` blocks obvious accidents, not a determined
  adversary; see that module's docstring. The guardrails above are a
  content-safety/quality layer, not a replacement for this.
- **Write operations** — the pipeline is intentionally read-only end to
  end (SELECT-only DB guard, output rail blocks any claimed write). Adding
  INSERT/UPDATE would need its own approval/audit design first.
