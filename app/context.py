"""
Shared run context for the agent pipeline.

The OpenAI Agents SDK threads one arbitrary context object through an
entire run (top-level agent + every nested agents-as-tools call) via
`RunContextWrapper`. We use that instead of having agents *say* what SQL
they ran or which agents were involved in their final text answer:
letting the LLM narrate its own tool usage is unreliable (it can
paraphrase, omit, or hallucinate a query). Recording facts as a side
effect of the tool functions themselves is deterministic and cannot
drift from what actually happened.
"""

from dataclasses import dataclass, field


@dataclass
class HotelAnalystContext:
    """Accumulates ground-truth facts about one /ask request as agents and
    tools execute. One instance is created per request and shared by every
    agent/tool in that run (see app/agents.py)."""

    sql_log: list[str] = field(default_factory=list)
    agents_used: list[str] = field(default_factory=list)

    def record_agent(self, name: str) -> None:
        if name not in self.agents_used:
            self.agents_used.append(name)

    def record_sql(self, sql: str) -> None:
        self.sql_log.append(sql)
