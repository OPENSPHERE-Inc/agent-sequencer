---
name: agent-sequencer
description: A mechanism for step-driving AI agents according to a classical program (sequencer program)
---

# agent-sequencer

This skill is an MCP server that implements an architecture where "a classical program drives an AI agent."
Decision logic is contained within the program, and the agent acts purely as a driver.

## Bundled programs

- `hello` — Minimal sample / smoke-test program. Greets each name in `params["names"]` (default `["world"]`) one at a time.
- `review-rounds` — Self-review helper for sequencer programs. Three specialists (python-sensei / sequencer-sensei / prompt-sensei) review → respond → verify, iterating up to N rounds until convergence.

For how to write new programs, see [`docs/authoring-programs.md`](docs/authoring-programs.md).

## Driving rules (mandatory)

1. When you receive a user request, run `sequencer_list_programs` to identify the appropriate program.
2. Start an instance with `sequencer_start`, then **memorize the returned `instance_id` as the highest priority**.
3. Execute `last_yield.text` (using your own tools).
4. Assemble the result according to `expect_schema` and submit it via `sequencer_next(for_step_no=<current value>, result=...)`.
5. While the returned `state` is `awaiting_result`, repeat steps 3 and 4.
6. If context is lost (e.g. due to compaction), re-synchronize with `sequencer_current`.
7. When `state` becomes `completed` / `aborted` / `failed`, **report the final result to the user and call `sequencer_close`**.
8. **The return value of `sequencer_current` must not be used as a basis for deciding what to do next.** Decisions must always follow the `last_yield` instruction itself.
9. When the user explicitly requests debugging, read the JSONL (under `~/.claude/sequencer/state/<id>.jsonl` or the directory specified by the `AGENT_SEQUENCER_STATE_DIR` environment variable) using Read. Do not stream history into context via MCP.

## MCP tool list

- `sequencer_list_programs` — List available programs.
- `sequencer_start` — Start an instance.
- `sequencer_current` — Re-fetch the most recent yield (for re-sync).
- `sequencer_next` — Submit result and obtain the next yield.
- `sequencer_resume` — Restore from JSONL.
- `sequencer_close` — Release (recommended path).
- `sequencer_list` — List active instances.

For details including setup and usage, see [`README.md`](README.md).
