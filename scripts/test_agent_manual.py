"""
Manual smoke test for the agent pipeline (section 4 checkpoint).
Requires a real OPENAI_API_KEY in .env — without one this will fail at the
API call, which still proves the agent wiring (imports, tool schemas,
context threading) is correct up to that point.

Run with: uv run python scripts/test_agent_manual.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analyst import ask


async def main():
    question = "Which reservation statuses exist, and how many reservations are in each?"
    response = await ask(question)

    print(f"Question: {question}")
    print(f"\nAnswer:\n{response.answer}")
    print(f"\nSQL run ({len(response.sql)} statement(s)):")
    for sql in response.sql:
        print(f"  - {sql}")
    print(f"\nAgents used: {response.agents_used}")
    print(f"Elapsed: {response.elapsed_seconds:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
