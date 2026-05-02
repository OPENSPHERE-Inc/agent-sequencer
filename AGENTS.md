# AGENTS.md — agent-sequencer

This file provides a brief overview for AI agents. For detailed project documentation, coding guidelines, architecture, and build instructions, see **[CLAUDE.md](CLAUDE.md)**.

## Quick Reference

- **Language**: Python ≥ 3.11
- **Dependency manager**: uv (Astral)
- **Transport**: MCP stdio
- **Distribution**: Claude Code plugin (git-based; PyPI deferred to Phase 2)
- **License**: MIT
- **Maintainer**: OPENSPHERE Inc.

## Key Files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Python package definition (Hatchling backend, name = `agent-sequencer`) |
| `src/agent_sequencer/__main__.py` | CLI entry point (`agent-sequencer` command) + search-path resolution |
| `src/agent_sequencer/api.py` | Yield types: `Instruction` / `Done` / `Abort` / `Progress` / `Context` / `StepFailed` |
| `src/agent_sequencer/runtime.py` | `Driver`: generator runtime + jsonschema validation + on_invalid retry/abort |
| `src/agent_sequencer/instance.py` | `Instance` + `InstanceStore` (per-instance `asyncio.Lock`) |
| `src/agent_sequencer/registry.py` | `ProgramRegistry`: program discovery, `compile() + exec()` loader, hot-reload `rescan()` |
| `src/agent_sequencer/persistence.py` | `EventLog` (JSONL), deterministic replay, disk-TTL prune |
| `src/agent_sequencer/tools.py` | The 7 MCP tools (`sequencer_list_programs` / `start` / `current` / `next` / `resume` / `close` / `list`) |
| `tests/` | pytest suite (Driver / registry / persistence / bundled review-rounds) |
| `.claude-plugin/plugin.json` | Claude Code plugin manifest |
| `.claude-plugin/marketplace.json` | Marketplace listing (the repo registers itself) |
| `.mcp.json` | Plugin-bundled MCP server registration (uses `${CLAUDE_PLUGIN_ROOT}`) |
| `skills/agent-sequencer/SKILL.md` | Driving rules loaded into agent context (kept short) |
| `skills/agent-sequencer/docs/authoring-programs.md` | Sequencer-program author's guide |
| `skills/agent-sequencer/programs/review_rounds.py` | Bundled review-rounds program |
| `skills/agent-sequencer/programs/review_rounds/` | Self-contained bundle (skills, agents, scripts) |
| `.github/workflows/ci.yml` | pytest matrix (3 OS × 3 Python) + `uv tool install` smoke test |
| `README.md` / `README_ja.md` | English README + its Japanese translation |

## Essential Rules

1. **Comments, docstrings, prompts, and READMEs are English.** Only `README_ja.md` may contain Japanese.
2. **Sequencer programs must be deterministic** — no `time.time()` / `random` / I/O inside `run()`. Delegate to the agent via an `Instruction` if a value is needed.
3. **Use `ctx.params.get(key, default)`**, never `ctx.params.get(key) or default` (the `or` form treats `[]`, `0`, `""` as missing).
4. **Every `Instruction` must specify `expect_schema`** with at least `type` and `required`.
5. **Never write to stdout from the server** — stdio is reserved for JSON-RPC. Logging goes to stderr.
6. **`sequencer_next` must run inside `instance.lock`**; re-check `is_terminal()` and `for_step_no` after acquiring.
7. **Persistence ordering**: write the `result` event before `Driver.send`, write the resulting `yield` event after — even if `send` raises.
8. **Do not pickle generators.** State recovery is via deterministic JSONL replay.
9. **Use `compile() + exec()` for program loading**, not `importlib`'s loader (its pyc cache silently runs stale code on same-size hot edits).
10. **`${CLAUDE_PLUGIN_ROOT}` only resolves under Claude Code plugin install**; in hand-written `.mcp.json` use absolute paths. `${workspaceFolder}` is **not** a Claude Code substitution.
11. **Code style**: PEP 8, type hints on public APIs, `from __future__ import annotations` at top of every module, Google-style docstrings.

For full details on architecture, lifecycle, MCP tool contracts, coding guidelines, CI/CD, and common tasks, refer to **[CLAUDE.md](CLAUDE.md)**.
