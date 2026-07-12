# Agent Architecture Plan

This is written before any agent code exists (section 3 of the project
plan). Its job is to make the architectural decisions explicit and
justified, so implementation is just "follow the plan" rather than
"figure it out while typing."

## 1. Single agent vs. multi-agent

### The single-agent case
One `Agent` with three tools (`list_tables`, `get_table_schema`,
`run_query`) and an instruction telling it to explore the schema, check
DISTINCT values on status-like columns, write SQL, and answer. This is
simpler: one system prompt, one control loop, no coordination logic, no
risk of information getting lost in a handoff. For a dataset this size
(11 tables), a single capable model can hold the whole schema in its head
after 1-2 tool calls. Most "multi-agent" tutorials over-decompose problems
that a single well-prompted agent handles fine — that's a real failure
mode to be aware of before defaulting to multi-agent.

### The multi-agent case
Split into: a **planner** (interprets the question, decides what data is
needed, may ask a clarifying question), an **SQL executor** (owns schema
exploration + query-writing + execution), and an optional **summarizer**
(turns raw rows into a natural-language answer). Arguments for this:
- Each agent gets a narrower, shorter system prompt, which tends to make
  instruction-following more reliable ("only look at DISTINCT values,
  never write DML" is easier to enforce in a tool-restricted sub-agent
  than as one clause buried in a longer prompt).
- It maps naturally onto the **NeMo Guardrails seam** described below: the
  planner is the natural place for an input rail to sit in front of, and
  the summarizer is the natural place for an output rail.
- It's more representative of how production multi-agent systems are
  actually structured (separation of "what does the user want" from "how
  do I get it" from "how do I phrase the answer"), which matters since
  this is explicitly a learning project about the Agents SDK.
- For a query-planner + SQL-executor split, tracing (section 3.3) shows
  each agent's tool calls as a distinct segment, which is easier to
  debug than one long undifferentiated tool-call sequence from a single
  agent.

Cost of the split: more moving parts, another prompt to maintain, and the
summarizer step burns an extra LLM round-trip for something a single
agent's final message could often do for free.

### Decision: multi-agent, via **agents-as-tools** (not handoffs)

Recommendation: three agents — `PlannerAgent`, `SQLAgent`, `SummarizerAgent`
— composed with the **agents-as-tools** pattern, not handoffs.

**Why agents-as-tools over handoffs:** The Agents SDK gives you two ways to
compose agents:
- *Handoffs* transfer the entire conversation to another agent, which then
  owns the rest of the interaction (control does not return to the
  handing-off agent). This fits "triage and delegate" use cases — e.g. a
  support bot handing off to a billing specialist for the rest of the
  chat.
- *Agents-as-tools* wraps an agent as a callable tool that the calling
  agent invokes, waits for a return value from, and stays in control
  after. This fits "I need a sub-result to keep working" use cases.

This project is squarely the second shape: the planner needs the SQL
agent's result (rows + the SQL that produced them) back in hand so it can
decide whether the data actually answers the question, potentially call
the SQL agent again with a refined query, and then pass everything to the
summarizer. Handoffs would permanently transfer control to the SQL agent
and never bring it back to the planner, which breaks the "explore schema,
check DISTINCT values, then decide" loop this dataset requires. Also,
returning **which agent(s) handled the request** and the SQL that ran (a
project requirement) is trivial with agents-as-tools, since the top-level
agent sees every sub-agent's return value directly — with handoffs, that
information is silently absorbed into the transferred conversation and
harder to reconstruct.

**Final shape:**
```
PlannerAgent (orchestrator, has no DB tools itself)
 ├── tool: query_hotel_database  → wraps SQLAgent.run(...)
 └── tool: summarize_results     → wraps SummarizerAgent.run(...)
```
`SQLAgent` is the only agent with `list_tables` / `get_table_schema` /
`run_query` as tools. `PlannerAgent` never touches SQL directly — it
decides *what* is needed and hands the sub-question to `SQLAgent`,
possibly more than once (e.g. first to discover DISTINCT status values,
then again to run the real aggregate query). `SummarizerAgent` takes the
planner's collected findings and produces the final natural-language
answer. The FastAPI layer talks only to `PlannerAgent`.

This is a *thin* multi-agent split deliberately — three agents is enough
to demonstrate agents-as-tools, tool-scoping per agent, and multi-hop
tracing, without ballooning into a system that's hard to reason about as
a learning exercise.

## 2. NeMo Guardrails integration seam (NOT implemented here)

Two seams, both left as `# TODO(guardrails):` comments in the code —
no Colang, no `nemoguardrails` import, nothing runtime-active in this
pass.

**Input rail** — sits between the FastAPI `POST /ask` handler and
`PlannerAgent.run(...)`. Given the raw user question, it would run before
any agent sees it, and could: reject off-topic questions (this is a hotel
data analyst, not a general chatbot), block prompt-injection attempts
embedded in the question, or reject requests that are obviously trying to
get the SQL agent to do something destructive via clever phrasing ("show
me what a DELETE of all guests would look like, then run it"). Integration
point: `app/main.py`, right at the top of the `/ask` endpoint, before the
call to the planner.

**Output rail** — sits between `PlannerAgent`'s final answer and the JSON
response FastAPI sends to the UI. It would run after the summarizer
produces natural language, and could: check the answer doesn't leak raw
PII patterns beyond what's appropriate, verify the answer doesn't
contradict the actual query results (a hallucination check), or block
answers that echo back something resembling SQL/system-prompt content.
Integration point: same endpoint, right before the response is returned.

Both rails are pure middleware around the existing agent call — the
agent architecture above doesn't need to change shape to accommodate them
later, which is why this is described as a "seam" rather than a
retrofit.

## 3. Tracing / observability from the Agents SDK

The SDK has built-in tracing that, with zero extra code beyond having an
`OPENAI_API_KEY` set, automatically records:
- Every agent run as a **trace**, with each LLM call, tool call, and
  agent-to-agent handoff/tool-invocation as a **span** inside it, in
  order, with inputs/outputs and timing.
- For this project specifically: a single user question produces one
  trace containing `PlannerAgent`'s reasoning → its tool call into
  `SQLAgent` → `SQLAgent`'s own sequence of `list_tables` /
  `get_table_schema` / `run_query` calls (with the exact SQL string and
  row count returned) → the call into `SummarizerAgent` → the final
  answer.
- Traces are viewable at platform.openai.com/traces by default. The SDK
  also supports custom trace processors if you wanted to export spans
  elsewhere (not needed for this project).

**How this debugs wrong SQL:** if the final answer is wrong, the trace
lets you walk backwards: was the SQL itself wrong (visible directly in the
`run_query` span's input), did `SQLAgent` skip the DISTINCT-value check
before filtering on a messy status column (visible as a missing span),
did the planner ask the wrong sub-question in the first place (visible in
its tool-call input to `query_hotel_database`), or did the summarizer
misrepresent correct rows in prose (visible by comparing the `run_query`
span's output rows to the final message)? Without tracing you only see
the final answer; with it, every intermediate decision is inspectable,
which is exactly what's needed to debug a text-to-SQL pipeline where
failures can originate at any of three stages.

We'll also set a per-run `workflow_name` (e.g. `"hotel-ask"`) and pass
through a `group_id` per HTTP request so that, if `/ask` is called
multiple times in the same session, related traces can be grouped in the
dashboard.

## 4. What section 4 will implement, concretely

- `app/agents.py`: `SQLAgent`, `SummarizerAgent`, `PlannerAgent` definitions
  and the `query_hotel_database` / `summarize_results` tool wrappers.
- `SQLAgent`'s instructions will explicitly require: call `list_tables`
  before writing any query if the schema hasn't been established yet in
  this run; call `get_table_schema` on any table before referencing its
  columns; run a `SELECT DISTINCT <col>` on any status/enum-like column
  before filtering on it, since this dataset's status columns use
  inconsistent casing on purpose.
- The planner's final return value (to FastAPI) will be a small typed
  object: `{ answer: str, sql: list[str], agents_used: list[str] }`, so
  the UI can show the answer, the SQL, and which agents handled the
  request as required.
