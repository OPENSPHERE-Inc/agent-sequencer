# agent-sequencer skill

An MCP skill that drives an AI agent as a step-execution debugger for a classical program
(a sequencer program written as a Python generator).

## Design documentation

- [Program author's guide](docs/authoring-programs.md)
- [Bundled programs README](programs/README.md)
- [review-rounds bundle README](programs/review_rounds/README.md)

For repository-wide topics (installation, distribution, CI, etc.) see the
[top-level README](../../README.md).

## Driving flow (summary)

1. When you receive a user request, run `sequencer_list_programs` to identify the appropriate program.
2. Start an instance with `sequencer_start`, then memorize the returned `instance_id` as the highest priority.
3. Execute `last_yield.text` (using your own tools).
4. Assemble the result according to `expect_schema` and submit it via `sequencer_next(for_step_no=<current value>, result=...)`.
5. When `state` becomes `completed` / `aborted` / `failed`, **report the final result to the user and call `sequencer_close`**.

For details, see [SKILL.md](SKILL.md) (the 9 driving rules).

## MCP tool list

| Tool | Role |
|---|---|
| `sequencer_list_programs` | List available programs |
| `sequencer_start` | Start an instance |
| `sequencer_current` | Re-fetch the most recent yield (for re-sync) |
| `sequencer_next` | Submit result and obtain the next yield |
| `sequencer_resume` | Restore from JSONL |
| `sequencer_close` | Release (recommended path) |
| `sequencer_list` | List active instances |

## Program search paths (first-match wins)

| Order | Path | Purpose |
|---|---|---|
| 1 | `$AGENT_SEQUENCER_PROGRAMS_DIR` | Bundled programs (set in the plugin's `.mcp.json`) / development overrides |
| 2 | `<cwd>/.claude/sequencer/programs/` | Project-specific programs |
| 3 | `~/.claude/sequencer/programs/` | User-wide programs |

The `programs/` directory bundled with the plugin (this directory) is passed via the
`AGENT_SEQUENCER_PROGRAMS_DIR` environment variable.

## How to invoke the skill

The agent-sequencer skill is a **foundation that provides a set of MCP tools** — it is not a
slash command that the user invokes directly as `/agent-sequencer`. Actual requests take
one of the following three forms:

### A. Specify the program by name (recommended)

```
Please run the review-rounds program with agent-sequencer
(max_rounds=3, base=main).
```

The agent confirms via `sequencer_list_programs`, then calls
`sequencer_start program="review-rounds" params={"max_rounds": 3, "base": "main"}`,
and executes the instructions returned in `last_yield.text` in sequence.

### B. Describe what you want done (the agent picks the program)

```
Please use agent-sequencer to review and fix my sequencer program at src/my_program.py.
```

The agent checks `sequencer_list_programs`, then selects a program that matches the
user's request (in this example, `review-rounds`) and starts it.

### C. Resume an interrupted instance

```
Please resume instance_id=abc123 and continue from where it stopped.
```

The agent restores the instance from JSONL with `sequencer_resume`, checks `last_yield`,
and resumes the loop. If `source_hash` does not match, a `ProgramChanged` error is
returned, and the agent reports this to the user.

## Development tips

| Goal | How |
|---|---|
| Reflect program edits immediately | Enable `--watch` (with a 2-second throttle) |
| Find the state directory location | Check `AGENT_SEQUENCER_STATE_DIR` (default: `~/.claude/sequencer/state/`) |
| View event history for debugging | Read `<state_dir>/<instance_id>.jsonl` |
| List active instances | `sequencer_list filter="active"` |
| Unit-test a program | Drive `Driver` directly (see [authoring-programs.md §11](docs/authoring-programs.md)) |

## Limitations (v1)

- Feedback re-fix loops (review-respond → review-resolve, up to 3 iterations) are not implemented.
- `ParallelInstructions` (in-program fan-out declarations) are not implemented.
- HTTP/SSE transport (sharing across multiple Claude Code sessions) is not implemented.
- Program sandboxing (stronger trust boundaries) is not implemented.
- Execution of TypeScript / Lua programs is not implemented.
