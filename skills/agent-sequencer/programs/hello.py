"""Sample sequencer program: greet each name in turn.

A minimal multi-step program useful as:
  - the smallest realistic example for new sequencer-program authors, and
  - a smoke test when verifying that agent-sequencer is wired up correctly
    (CLI entry point, MCP server, plugin install, ``--watch``, etc.).

For each name in ``params["names"]`` (default ``["world"]``) the program asks
the AI agent to produce a one-line greeting, validates the reply against a
JSON Schema, and aggregates the results into the final ``Done.summary``.
"""

from __future__ import annotations

from agent_sequencer.api import Abort, Done, Instruction

NAME = "hello"
DESCRIPTION = (
    "Greet each name in turn. Minimal sample / smoke-test program "
    "useful for first-time agent-sequencer setup verification."
)

PARAMS_SCHEMA = {
    "names": {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "minItems": 1,
        "default": ["world"],
        "description": "Names to greet, in order.",
    },
}

_GREETING_SCHEMA = {
    "type": "object",
    "properties": {"message": {"type": "string", "minLength": 1}},
    "required": ["message"],
    "additionalProperties": True,
}


def run(ctx):
    """One Instruction per name; aggregate the messages into the final summary."""
    # Use ctx.params.get(key, default) — never ``or default`` (it would treat
    # an empty list as missing and silently fall through to the default).
    names = ctx.params.get("names", ["world"])
    if not names:
        yield Abort(reason="`names` must contain at least one entry.")
        return

    total = len(names)
    greetings: list[dict] = []
    for index, name in enumerate(names, start=1):
        ctx.publish_progress(current=index, of=total, label=f"greeting {index}/{total}")
        result = yield Instruction(
            text=(
                f"[Step {index}/{total}] Produce a one-line, friendly greeting "
                f"for '{name}'. Respond with JSON: "
                f'{{"message": "<your greeting>"}}.'
            ),
            expect_schema=_GREETING_SCHEMA,
            timeout_minutes=2,
        )
        greetings.append({"name": name, "message": result["message"]})

    yield Done(
        summary={
            "greeted_count": len(greetings),
            "greetings": greetings,
        }
    )
