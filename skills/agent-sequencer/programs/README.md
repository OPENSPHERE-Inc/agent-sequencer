# Sequencer program directory (bundled with the plugin)

*[日本語版](README_ja.md)*

This directory holds the sequencer programs bundled with the plugin.

## Search path priority

`registry.py` searches for programs in the following order (first match wins):

1. `$AGENT_SEQUENCER_PROGRAMS_DIR` (environment variable)
2. `<cwd>/.claude/sequencer/programs/` (project-specific)
3. `~/.claude/sequencer/programs/` (user-wide)

This directory (`skills/agent-sequencer/programs/`), bundled with the plugin, is
**specified by the plugin's `.mcp.json` via `AGENT_SEQUENCER_PROGRAMS_DIR`**.
It is not part of the MCP server's automatic search path (because the PyPI install
and plugin install live in different locations).

## Bundled programs

| File | Name | Overview |
|---|---|---|
| `hello.py` | `hello` | Minimal sample / smoke-test program. Greets each name in `params["names"]` (default `["world"]`) one at a time. Useful as a starting point for new program authors and for verifying that agent-sequencer is wired up correctly. |
| `review_rounds.py` | `review-rounds` | Reviews a sequencer program with three specialists (python-sensei / sequencer-sensei / prompt-sensei), then responds and verifies, iterating up to N rounds until convergence. Useful as a self-review helper for your own sequencer programs. |

The skills, agents, and scripts that `review_rounds.py` references are self-contained
in the adjacent `review_rounds/` directory. See its
[`README.md`](review_rounds/README.md) for details.
