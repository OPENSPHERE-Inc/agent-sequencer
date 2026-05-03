# CLAUDE.md — agent-sequencer

## Project Overview

**agent-sequencer** is an MCP server + Claude Code skill that lets an AI agent step-execute
a classical program (a "sequencer program" written as a Python generator) the way a debugger
drives a process. Decision logic is owned by the program; the AI agent acts as a thin driver
that executes each yielded `Instruction` and submits the result back. Guardrails live in code,
not in prompts, so long-running tasks remain stable as the conversation context degrades.

It is developed and maintained by **OPENSPHERE Inc.** under the MIT license.

- Repository language: **Python ≥ 3.11**
- Dependency / environment manager: **uv** (Astral)
- Distribution: **Claude Code plugin** (git-based; PyPI publication deferred to Phase 2)
- Transport: **MCP stdio** (one server per Claude Code session)
- Current version: **0.1.0**

---

## Repository Layout

```
agent-sequencer/
├── README.md                            # English top-level README (entry point)
├── README_ja.md                         # Japanese translation of the top README
├── LICENSE                              # MIT
├── pyproject.toml                       # Python package definition (Hatchling build backend)
├── uv.lock                              # uv lockfile
├── .gitignore / .gitattributes          # LF/CRLF unification
├── .mcp.json                            # Plugin-bundled MCP server registration
│
├── src/
│   └── agent_sequencer/                 # Python package
│       ├── __init__.py                  # __version__
│       ├── __main__.py                  # CLI entry point (agent-sequencer command)
│       ├── api.py                       # Yield types: Instruction / Done / Abort / Progress / Context / StepFailed
│       ├── runtime.py                   # Driver: generator runtime + jsonschema validation + on_invalid retry
│       ├── instance.py                  # Instance + InstanceStore (per-instance asyncio.Lock)
│       ├── registry.py                  # ProgramRegistry: program discovery, compile()+exec() loader, hot-reload rescan
│       ├── persistence.py               # JSONL EventLog, deterministic replay, disk-TTL prune
│       └── tools.py                     # 7 MCP tools (sequencer_list_programs / start / current / next / resume / close / list)
│
├── tests/                               # pytest test suite
│   ├── conftest.py                      # Shared fixtures (bundled_programs_dir, state_dir)
│   ├── test_runtime.py                  # Driver behaviour
│   ├── test_registry.py                 # Program loading, first-match wins, hot rescan diff
│   ├── test_persistence.py              # EventLog append/read, prune by TTL
│   └── test_review_rounds.py            # End-to-end Driver run of the bundled review-rounds program
│
├── .claude-plugin/
│   ├── plugin.json                      # Claude Code plugin manifest
│   └── marketplace.json                 # Marketplace listing (the repo registers itself)
│
├── skills/
│   └── agent-sequencer/                 # The Claude Code skill (auto-loaded by the plugin)
│       ├── SKILL.md                     # Driving rules (kept short; loaded into agent context)
│       ├── README.md                    # Skill-level README
│       ├── docs/
│       │   └── authoring-programs.md    # Sequencer-program author's guide
│       └── programs/                    # Plugin-bundled sequencer programs (passed via AGENT_SEQUENCER_PROGRAMS_DIR)
│           ├── README.md
│           ├── review_rounds.py         # The bundled review-rounds program
│           └── review_rounds/           # Self-contained bundle (skills, agents, scripts)
│               ├── README.md
│               ├── agents/              # python-sensei / sequencer-sensei / prompt-sensei
│               ├── scripts/             # fetch-diff.sh / rm-tmp.sh / render-review.py
│               └── skills/              # sequencer-review / sequencer-review-respond / sequencer-review-resolve
│
└── .github/
    └── workflows/
        └── ci.yml                       # pytest matrix (3 OS × 3 Python) + uv-tool-install smoke test
```

---

## Build / Run Instructions

### Prerequisites

- **uv** ≥ 0.5 — Required. Install via the official script (`curl -LsSf https://astral.sh/uv/install.sh | sh` on Unix; the PowerShell installer on Windows). Avoid pip-installed uv.
- **Python** 3.11–3.13 — uv will fetch a managed Python interpreter automatically if none is on PATH.

No pre-`pip install` step is needed — `uv` resolves and installs into a project-local `.venv/`.

### Setup

```bash
git clone https://github.com/OPENSPHERE-Inc/agent-sequencer.git
cd agent-sequencer
uv sync                  # install runtime + dev dependencies
uv run agent-sequencer --help
```

### Run the MCP server (development mode)

```bash
uv run --project . agent-sequencer --watch
```

`--watch` enables hot reload of `programs/*.py` (2-second throttle). The server speaks
JSON-RPC over stdio — when launched from a terminal it just sits waiting for client input.
Real use is via Claude Code; see `.mcp.json` and the README's "Development setup" section.

### Run tests

```bash
uv run pytest                # all tests
uv run pytest tests/test_runtime.py -v
```

### Verify the package builds for distribution

```bash
uv tool install .
agent-sequencer --help       # entry point installed via wheel
uv tool uninstall agent-sequencer
```

CI runs this exact flow on every push (Linux / macOS / Windows × Python 3.11 / 3.12 / 3.13).

---

## Architecture & Key Concepts

### Communication model

```
+---------+   MCP tool call    +-------------------------+
| Claude  | -----------------> |     agent-sequencer     |
|  Code   |                    |  (MCP server, stdio)    |
| (driver)| <----------------- |                         |
+---------+   next instruction |  +-------------------+  |
     |                         |  | Sequencer program |  |
     | runs with own tools     |  | (Python generator)|  |
     | (Bash/Edit/Read/Skill)  |  +-------------------+  |
     v                         |  +-------------------+  |
   user                        |  | InstanceStore +   |  |
                               |  | JSONL event log   |  |
                               |  +-------------------+  |
                               +-------------------------+
```

The MCP server is a **driver of a Python generator**, not a workflow engine. A sequencer
program is just a function `def run(ctx)` that `yield`s `Instruction(...)`, receives the
agent's validated JSON result on the next iteration, and `yield`s `Done(...)` /
`Abort(...)` to terminate.

### Core components

- **Yield types** (`api.Instruction` / `Done` / `Abort` / `Progress` / `StepFailed`) — What a sequencer program may `yield`. `Instruction.expect_schema` is a JSON Schema that validates the agent's reply. `on_invalid` ∈ {`retry`, `abort`}.
- **Context** (`api.Context`) — Passed to `run(ctx)`. Exposes `params`, `env`, `publish_progress(...)`.
- **Driver** (`runtime.Driver`) — Owns one generator instance. `start()` advances to the first yield; `send(result)` validates the result against `expect_schema` and advances (or re-issues with a fresh `step_no` on retry). `throw(exc)` injects a `StepFailed` into the program.
- **InstanceStore** (`instance.InstanceStore`) — Process-wide registry of `Instance` objects. Each instance carries its own `asyncio.Lock` so concurrent `sequencer_next` calls on the same `instance_id` serialise.
- **ProgramRegistry** (`registry.ProgramRegistry`) — Discovers `programs/*.py`, loads each via `compile() + exec()` (deliberately bypasses `importlib`'s pyc cache so equal-size hot edits are picked up), records `source_hash` for resume integrity. `rescan()` returns an added/updated/removed diff.
- **EventLog** (`persistence.EventLog`) — Append-only JSONL writer. Three event kinds: `header`, `yield`, `result`. `read_events()` + `parse_header()` + `iter_results()` support deterministic replay. `prune_old_logs()` deletes logs whose last yield is past their disk-TTL.
- **MCP tools** (`tools.build_server()`) — Registers the 7 MCP tools onto a FastMCP server. `--watch` wraps registry-touching tools with a throttled `rescan()`.

### Determinism contract (most important constraint)

A sequencer program **must** be deterministic. The replay-based `sequencer_resume` re-runs
the program from scratch and re-injects the recorded `result` events; if the program reads
the wall clock, calls `random`, performs I/O, or mutates module state it will diverge.

Operationally this means program authors must:

- Never call `time.time()` / `datetime.now()` / `random.*` directly. Ask the agent via an
  `Instruction` if a value is required (`{"timestamp": "..."}`).
- Never perform file/network/DB I/O inside `run()`.
- Use `ctx.params.get(key, default)` rather than `ctx.params.get(key) or default` (the
  `or` form treats `[]`, `0`, and `""` as missing).
- Avoid module-level heavy initialization (`--watch` re-execs the module on rescan).

The full author guidelines live in `skills/agent-sequencer/docs/authoring-programs.md`.

### Lifecycle

```
sequencer_start ─▶ RUNNING ─▶ AWAITING_RESULT ⇄ (sequencer_next) ─▶ TERMINAL_QUERYABLE
                                                                            │
                              (Done / Abort / Exception)                    │
                                                                            ▼
                                                         (close / TTL / server stop)
                                                                            │
                                                                            ▼
                                                                       ARCHIVED
                                                                            │
                                                                       (disk TTL)
                                                                            ▼
                                                                         PRUNED
```

- In-memory TTL after terminal — 10 min.
- Disk TTL (Done) — 30 days.
- Disk TTL (Abort / interrupted) — 7 days.
- Disk TTL (Failed / exception) — 90 days.

Prune runs once at server start. The age basis is the **last yield's `yielded_at`**
(falling back to `mtime` if the timestamp is unparseable).

### MCP tools

All tools accept and return JSON; `instance_id` is mandatory on every tool that touches an
instance because MCP does not expose caller identity (see `implementation-plan.md` §4 for
the rationale).

- `sequencer_list_programs` — Enumerate loaded programs. With `--watch`, triggers a throttled rescan first.
- `sequencer_start` — Create a new instance from a program + params. Returns `instance_id`, `step_no`, `last_yield`.
- `sequencer_current` — Re-fetch the most recent yield (used after compact / interrupt to re-sync). Read-only; never used as a decision input.
- `sequencer_next` — Validate `result` against the pending `Instruction.expect_schema`, then `gen.send(result)`. Mandatory `for_step_no` rejects out-of-sync submissions.
- `sequencer_resume` — Rebuild a Driver from JSONL via deterministic replay. `source_hash` mismatch returns `ProgramChanged`.
- `sequencer_close` — Idempotent release. `delete_log=True` also removes the JSONL.
- `sequencer_list` — Enumerate active or terminal instances (metadata only — never returns `last_yield` text, to keep context small).

---

## Code Style & Formatting

- **Python style**: PEP 8, type hints required on public APIs, `from __future__ import annotations` at the top of every module.
- **Docstrings**: Google style (Args / Returns / Raises). Module docstrings open with a one-line summary.
- **Comments / docstrings / prompts / READMEs are in English.** The only Japanese file in the repo is `README_ja.md` (a translation of the top README).
- **Strings in `Instruction.text` and bundled skill `.md` files are AI-facing prompts.** Keep them imperative, structured, and free of decorative Markdown — Claude reads them, not humans.
- **No formatter is enforced yet.** When ruff/black is added (Step 10 follow-up) the line length will be 100 cols.
- **Imports**: standard library → third-party → local, separated by blank lines.

---

## Coding Guidelines

### Sequencer program authors

The full guide is `skills/agent-sequencer/docs/authoring-programs.md`. The non-negotiable rules:

1. Define `NAME` (kebab-case), `DESCRIPTION`, `PARAMS_SCHEMA`, and `def run(ctx)`.
2. Every `Instruction` must specify `expect_schema` with at least `type` and `required`.
3. Always end with an explicit `yield Done(summary={...})` or `yield Abort(reason="...")`.
4. Stay deterministic (see the constraint section above).
5. If the program ships supporting files (sub-skills, agent definitions, scripts), bundle
   them in an adjacent `<program-name>/` directory and resolve paths via
   `Path(__file__).parent / "<program-name>"` so the bundle is install-location independent
   (see `programs/review_rounds.py` for the canonical example).

### Server / runtime contributors

1. **Lock discipline**: `sequencer_next` must run inside `instance.lock`. After acquiring,
   re-check `is_terminal()` and `for_step_no` because the state may have advanced while
   the call was waiting.
2. **Persistence ordering**: write the `result` event **before** calling `Driver.send`,
   then write the resulting `yield` event **after** — even if `send` raises. The JSONL
   must always reflect what was attempted, not just what succeeded.
3. **No stdout output from the server**. stdout is reserved for JSON-RPC. Use `logger`
   (configured to stderr in `__main__.py`).
4. **Avoid module-level state in `programs/*.py`** — the registry re-execs them on rescan.
5. **Do not pickle generators**. State is recovered through deterministic replay.
6. **Search-path resolution**: tier 3 (auto-derived "skill-root/programs") was removed in
   the public-repo restructure. Bundled programs are now passed via the
   `AGENT_SEQUENCER_PROGRAMS_DIR` env var (set by the plugin's `.mcp.json`). Don't
   reintroduce a `__file__`-based fallback in `__main__.py` — it can't span PyPI vs.
   plugin install layouts.

### Naming conventions

- **Modules / functions / variables**: `snake_case`.
- **Classes**: `PascalCase` (e.g., `Driver`, `ProgramRegistry`, `EventLog`).
- **Constants / module-level state**: `UPPER_SNAKE_CASE` (`STATE_AWAITING_RESULT`, `KIND_DONE`, `DISK_TTL_COMPLETED`).
- **Private helpers**: leading underscore (`_resolve_search_paths`, `_last_yield_event`).
- **MCP tool names**: `sequencer_<verb>[_<noun>]` (`sequencer_list_programs`, `sequencer_close`).
- **Sequencer program file names**: `snake_case.py`. The `NAME` constant inside is `kebab-case`.

### Error handling

- The MCP layer translates internal exceptions into typed error codes:
  `NotFound` / `AlreadyLoaded` / `CorruptedLog` / `ProgramChanged` / `ProgramNotFound` /
  `ReplayFailed` / `OutOfSync` / `BusyError` / `TerminalError` / `NotLoaded`.
- Use `logger.warning` for recoverable conditions (e.g., a JSONL line that fails to parse
  is skipped with a warning, not raised).
- Never let an exception from a sequencer program propagate untouched — the Driver wraps
  it into a terminal `error` yield with `state=failed`.

---

## Testing & Validation

- **`pytest`** is the only supported test runner. Tests live in `tests/`. `pyproject.toml`
  configures `testpaths = ["tests"]` and `asyncio_mode = "auto"`.
- **Drive sequencer programs through `Driver` directly** for unit tests — no MCP server
  needed (see `tests/test_review_rounds.py` for the pattern).
- **State directory**: tests use `tmp_path` via the `state_dir` fixture so they never
  touch the real `~/.claude/sequencer/state/`.
- **Encoding**: always pass `encoding="utf-8"` to `Path.open` / `Path.read_text`. On
  Windows the default codepage is cp932 and will corrupt the JSONL.
- **Windows file locking**: a JSONL file held open by an `EventLog` cannot be `unlink`ed
  on Windows. Tests that delete logs must `close()` first.

When adding new functionality, also verify:

- `uv run agent-sequencer --help` still works (entry point intact).
- The `install-from-git` job equivalent (`uv tool install .`) still produces a runnable CLI.
- The bundled `review-rounds` program still loads and its three convergence branches still hit (`tests/test_review_rounds.py`).

---

## CI / CD

- `.github/workflows/ci.yml` (trigger: push / PR to `master` / `main`) — (a) `pytest` matrix on Linux × macOS × Windows × Python 3.11 / 3.12 / 3.13; (b) `uv tool install .` smoke test that validates the wheel is consumable by `uvx --from git+...`.

There is currently no `publish.yml` (PyPI publication is a Phase 2 task).

Releases follow semver: `0.X.Y` for pre-1.0, `X.Y.Z` post-1.0.

---

## Common Tasks for AI Agents

### Adding a new MCP tool

1. Define the tool inside `tools.build_server()` using FastMCP's `@server.tool()` decorator.
   Required params and types are derived from the function signature.
2. Decide the contract:
   - Lock-required? (any tool that mutates instance state must run inside `instance.lock`)
   - Watch-rescan-required? (any tool that consults `ProgramRegistry`)
   - Permitted in which lifecycle states? (see `implementation-plan.md` §4.3)
3. Add a unit test in `tests/test_tools.py` (file does not exist yet — create it).
4. Update `skills/agent-sequencer/SKILL.md` driving rules and `README.md` tool table.
5. Add the tool to the `permissions.allow` list in the README's "MCP tool permissions" example.

### Adding a new bundled sequencer program

1. Create `skills/agent-sequencer/programs/<name>.py` defining `NAME` / `DESCRIPTION` / `PARAMS_SCHEMA` / `run(ctx)`.
2. If it has supporting assets, place them in `skills/agent-sequencer/programs/<name>/` (the registry only scans `*.py`, so subdirectories are safe).
3. Resolve any in-bundle paths via `Path(__file__).resolve().parent / "<name>"` — never hardcode repo-relative paths.
4. Add a unit test under `tests/` that drives the program through `Driver` and asserts each branch.
5. Add the program to the `programs/README.md` table and the SKILL.md "Bundled programs" section.

### Modifying `Driver` / `runtime.py`

- The `_advance` / `send` / `throw` methods are the only places that touch the generator. Keep the order [validate → write `result` event → `gen.send` → write `yield` event] intact, otherwise replay will diverge.
- The `Done.summary` / `Abort.reason` extraction supports two paths: yielded `Done(...)` and `return {...}` (which surfaces as `StopIteration.value`). Both must keep producing terminal yields with the right `kind`.

### Modifying `persistence.py`

- The JSONL format is part of the on-disk contract. Adding a new event kind requires a migration path for older logs (or a `min_log_version` check in `parse_header`).
- TTL constants (`DISK_TTL_*`) are public — bumping them changes user-visible disk retention.

### Modifying `ProgramRegistry`

- Use `compile() + exec()`, not `importlib.util.spec_from_file_location` / `exec_module`. The latter caches `.pyc` files keyed on (mtime, size) and silently runs stale code when an edit produces a same-size, same-second rewrite — which broke `--watch` testing in step 7.
- The `source_hash` is `sha256(read_bytes())` — keep it that way so the value stays stable across platforms / line endings (the repo enforces LF via `.gitattributes`).

### Translation

- Any code change that adds new user-facing prose in `README.md` requires a matching update in `README_ja.md`. The two files are kept in sync manually for now.
- All other docs / code comments / prompts stay English-only.

---

## Important Warnings

- **Never write to stdout from inside the server** — stdio is reserved for JSON-RPC frames. Logging is configured to stderr in `__main__.py`; preserve that.
- **`${CLAUDE_PLUGIN_ROOT}` is only resolved by Claude Code** when the server is launched from a plugin's `.mcp.json`. In a hand-written `.mcp.json` (the development-mode example in the README) it must be replaced with an absolute path.
- **`${workspaceFolder}` is NOT a Claude Code substitution.** Only VS Code expands that token; Claude Code passes it through verbatim. Always use absolute paths or `${CLAUDE_PLUGIN_ROOT}`.
- **The bundled `review_rounds` skill `.md` files reference each other and the bundle scripts via `skills/agent-sequencer/programs/review_rounds/...` paths.** That works when Claude Code's cwd is the agent-sequencer repo root. For plugin install (where cwd ≠ plugin root) the path becomes wrong; making it install-location independent is a tracked Step 10 follow-up.
- **Determinism is enforced by convention, not by sandbox.** A program that calls `time.time()` will silently diverge on replay. Code review is the only line of defence.
- **`AGENT_SEQUENCER_STATE_DIR` may contain unfinished sessions across crashes.** The startup `prune_old_logs` only removes entries past their disk TTL — fresh failures stay around for 90 days for post-mortem.
- **Hot reload (`--watch`) does not affect running instances.** A Driver captures its `run_fn` reference at `start()`; subsequent rescans only update the registry for new `sequencer_start` calls.
- **`.gitattributes` forces LF for source files.** Editing on Windows is fine, but do not configure `core.autocrlf=true` globally — it bypasses the attribute and breaks `source_hash` reproducibility.
- **Python 3.11 is the floor.** `from __future__ import annotations` and PEP 604 unions (`X | Y`) are used throughout.

---

## Agent Teams

### Communication language

- AI agents must respond in the language the user is using (Japanese ↔ English).
- Code, comments, prompts, and documentation written into the repo must remain in English.
  Only `README_ja.md` is permitted to contain Japanese prose.

### Team leader policy

- The team leader focuses exclusively on orchestrating teammates and does not edit files itself.

### Team creation policy

- Team size: 3–5 members chosen by task.
- Each teammate works on different files to avoid edit conflicts.
- Begin with research / review, then dispatch the team for parallel execution.
- Do not use sub-agents for tasks that fit a team. **Exception**: parallel reviews and bulk translation may use sub-agents.

### Available specialist agents

Custom agent definitions live under `.claude/agents/` (when present in your local Claude Code setup). Recommended specialists for this repo:

- **python-sensei** — Python language correctness, PEP compliance, type hints, async/await, generator semantics.
- **sequencer-sensei** — agent-sequencer API itself: `Instruction` / `Done` / `Abort` / `Context`, determinism, lifecycle, `expect_schema` design, bundling.
- **prompt-sensei** — `Instruction.text` design: structure, schema integrity, runaway-prevention constraints, template patterns.
- **devops-sensei** — `pyproject.toml`, `uv.lock`, GitHub Actions, plugin packaging.

The bundled `review_rounds` program already drives the first three of these in parallel as part of its review cycle.
