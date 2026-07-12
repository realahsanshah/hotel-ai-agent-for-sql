"""
NeMo Guardrails integration — the input/output rails that AGENT_PLAN.md
section 2 originally described as a TODO seam, now implemented.

Deliberately kept separate from app/agents.py: the rails here never touch
the hotel database or the OpenAI Agents SDK pipeline, they only ever ask
the guardrails model a focused yes/no question about a piece of text (see
guardrails/prompts.yml). app/main.py calls check_input() before the
pipeline runs and check_output() after, exactly at the two TODO(guardrails)
comments that were already there.

How "blocked vs allowed" is detected: NeMo Guardrails' self-check flows
either return the input message unchanged (allowed) or a refusal message
from the flow (blocked) — see nemoguardrails/library/self_check/*/flows.co.
We detect which happened by comparing the returned text to what we sent
in, rather than string-matching a specific refusal phrase, so a custom
refusal wording still works correctly.
"""

import logging
from pathlib import Path

from nemoguardrails import LLMRails, RailsConfig
from nemoguardrails.rails.llm.options import GenerationOptions, GenerationRailsOptions

logger = logging.getLogger(__name__)

GUARDRAILS_CONFIG_PATH = Path(__file__).resolve().parent.parent / "guardrails"

# Loaded once at import time (parses config.yml + prompts.yml) and reused
# for every request — this mirrors how app/agents.py's Agent objects are
# module-level singletons rather than rebuilt per call.
_config = RailsConfig.from_path(str(GUARDRAILS_CONFIG_PATH))
_rails = LLMRails(_config)

# Only run the named rail category; dialog/retrieval stay off because we
# are never letting this LLMRails instance generate the actual answer —
# that stays the OpenAI Agents SDK pipeline's job (app/agents.py).
_INPUT_ONLY = GenerationOptions(
    rails=GenerationRailsOptions(
        input=True, output=False, dialog=False, retrieval=False,
        tool_input=False, tool_output=False,
    )
)
_OUTPUT_ONLY = GenerationOptions(
    rails=GenerationRailsOptions(
        input=False, output=True, dialog=False, retrieval=False,
        tool_input=False, tool_output=False,
    )
)


async def check_input(question: str, history: str | None = None) -> tuple[bool, str | None]:
    """Runs the input rail (topic + prompt-injection check) on a raw user
    question, before it reaches any agent. Returns (allowed, refusal) —
    refusal is only set when blocked.

    `history` (see app/analyst.py's get_recent_history_text) is recent
    conversation text for this session, if any. Without it, a legitimate
    follow-up like "list of it?" reads as gibberish/off-topic in isolation
    — self_check_input only ever judges the text it's given, and has no
    other way to know what "it" refers to. Bundling history into the
    checked text (rather than, say, ignoring short messages) keeps the
    policy enforcement uniform: the guardrails/prompts.yml prompt is told
    explicitly to judge only the latest message, using the rest as context."""
    message = question
    if history:
        message = (
            f"Recent conversation (for context only):\n{history}\n\n"
            f"Latest message to evaluate: {question}"
        )
    result = await _rails.generate_async(
        messages=[{"role": "user", "content": message}],
        options=_INPUT_ONLY,
    )
    reply = result.response[0]["content"] if result.response else ""
    if reply == message:
        return True, None
    logger.warning("Input guardrail blocked question: %r (history=%s)", question, bool(history))
    return False, reply


def fallback_answer_from_evidence(evidence: list[str]) -> str:
    """Used by app/main.py when check_output() blocks an answer. A blank
    "I can't respond to that" is a dead end for questions that DID have a
    correct answer available — most output-rail blocks in this app are the
    fact-check flow catching the summarizer inventing a number, not an
    actual unsafe/policy violation (see AGENT_PLAN.md section 2 for a
    worked example). The safety checks in guardrails/prompts.yml
    (self_check_output) only ever concern the CHATBOT'S OWN WORDING —
    abusive language, leaked internals, false write claims — none of which
    make the underlying query evidence itself unsafe to show. So instead of
    hiding everything, we show the user exactly what the database actually
    returned and say plainly that the prose summary wasn't trustworthy."""
    if not evidence:
        return (
            "I wasn't able to produce a reliable answer to that — no "
            "verified data was found to back up a response."
        )
    return (
        "I retrieved the right data but couldn't reliably summarize it in "
        "words, so here is exactly what the database query returned "
        "instead:\n\n" + "\n\n".join(evidence)
    )


async def check_output(question: str, answer: str, evidence: list[str]) -> tuple[bool, str | None]:
    """Runs the output rail (safety check + fact-check against the real SQL
    results in `evidence`) on the pipeline's final answer, before it's
    returned to the UI. Returns (allowed, refusal)."""
    messages = []
    if evidence:
        # check_facts=True opts into the (expensive) fact-checking flow per
        # call — see guardrails/config.yml and the flow's own docstring in
        # nemoguardrails/library/self_check/facts/flows.co. Skipped entirely
        # when there's no evidence (e.g. an off-topic-adjacent answer that
        # never queried the database) since there's nothing to check against.
        messages.append({
            "role": "context",
            "content": {"relevant_chunks": "\n\n".join(evidence), "check_facts": True},
        })
    messages.append({"role": "user", "content": question})
    messages.append({"role": "assistant", "content": answer})

    result = await _rails.generate_async(messages=messages, options=_OUTPUT_ONLY)
    reply = result.response[0]["content"] if result.response else ""
    if reply == answer:
        return True, None
    logger.warning("Output guardrail blocked answer: %r", answer)
    return False, reply
