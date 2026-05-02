# agent-sequencer

*[日本語版 README](README_ja.md)*

> **An MCP skill + server that drives an AI agent like a debugger driving a process, stepping it through a classical program (a Python generator).**

The truth of the logic stays on the **program side**, while the AI acts purely as the **driver**.
Because guardrails live in code rather than in prompts, the system is resilient to context degradation across long-running tasks.

- **Supported editors**: Claude Code
- **Language / runtime**: Python ≥ 3.11
- **Distribution**: Claude Code plugin (git-based)
- **License**: MIT

---

## Features

- When you have an AI run a long, multi-step task, the **outer-loop decisions stay in the program** and the AI only executes individual steps.
- Recovery from schema violations, interruptions, and post-compact desynchronization is handled by **JSONL event logs + deterministic replay**.
- The bundled `review-rounds` program reviews → fixes → verifies your own sequencer programs using three specialist agents (python-sensei / sequencer-sensei / prompt-sensei).

For details, see [`skills/agent-sequencer/SKILL.md`](skills/agent-sequencer/SKILL.md) and
[`skills/agent-sequencer/docs/authoring-programs.md`](skills/agent-sequencer/docs/authoring-programs.md).

---

## Installation

### Prerequisites

- [uv](https://docs.astral.sh/uv/) (used to run the MCP server). If not yet installed:
  ```powershell
  # Windows (PowerShell)
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```
  ```bash
  # macOS / Linux
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

### Option A: Install as a Claude Code plugin (recommended)

```text
/plugin marketplace add OPENSPHERE-Inc/agent-sequencer
/plugin install agent-sequencer@agent-sequencer
```

This sets up the skill, the bundled programs, and the MCP server automatically.
The plugin's `.mcp.json` invokes `uv run` via `${CLAUDE_PLUGIN_ROOT}` and passes
the bundled `programs/` to the MCP server through `AGENT_SEQUENCER_PROGRAMS_DIR`.

### Option B: Clone and use as a developer

```bash
git clone https://github.com/OPENSPHERE-Inc/agent-sequencer.git
cd agent-sequencer
uv sync
uv run agent-sequencer --help
```

If you launch Claude Code with this directory as cwd, the repository's bundled
[`.mcp.json`](.mcp.json) and [`skills/agent-sequencer/`](skills/agent-sequencer/)
are loaded. The `${CLAUDE_PLUGIN_ROOT}` variable is only expanded inside the
Claude Code plugin context, so for development you need to either create a
separate `.claude/.mcp.json` or set the relevant environment variables (see
[Development setup](#development-setup)).

---

## Quick start

Once the plugin is enabled in Claude Code, you can ask in natural language:

### A. Specify a program by name

```
Run the review-rounds program with agent-sequencer
(max_rounds=3, base=main)
```

### B. Describe what you want

```
Use agent-sequencer to review and fix src/my_program.py
```

The agent will automatically follow the flow `sequencer_list_programs` → `sequencer_start` → drive loop → `sequencer_close`.

### C. Resume an interrupted instance

```
Resume instance_id=abc123 and continue from where it stopped
```

---

## Development setup

Example `.mcp.json` for cloning the repository and running the MCP server directly
(use a local config file so you don't overwrite `<repo>/.mcp.json`):

```jsonc
// ~/.claude/.mcp.json or <project>/.claude/.mcp.local.json
{
  "mcpServers": {
    "agent-sequencer": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "run",
        "--project",
        "/absolute/path/to/agent-sequencer",
        "agent-sequencer",
        "--watch"
      ],
      "env": {
        "AGENT_SEQUENCER_PROGRAMS_DIR": "/absolute/path/to/agent-sequencer/skills/agent-sequencer/programs",
        "AGENT_SEQUENCER_STATE_DIR": "${HOME}/.claude/sequencer/state",
        "VIRTUAL_ENV": "",
        "UV_LINK_MODE": "copy"
      }
    }
  }
}
```

`--watch` enables hot reload during development (it picks up changes to `programs/*.py` with a 2-second throttle).

### MCP tool permissions

Add the following to the allow list in `.claude/settings.local.json` (or similar):

```jsonc
"permissions": {
  "allow": [
    "mcp__agent-sequencer__sequencer_list_programs",
    "mcp__agent-sequencer__sequencer_start",
    "mcp__agent-sequencer__sequencer_current",
    "mcp__agent-sequencer__sequencer_next",
    "mcp__agent-sequencer__sequencer_resume",
    "mcp__agent-sequencer__sequencer_close",
    "mcp__agent-sequencer__sequencer_list"
  ]
}
```

### Tests

```bash
uv run pytest
```

---

## Directory layout

```
agent-sequencer/
├── pyproject.toml                     # Python package (MCP server)
├── src/agent_sequencer/               # Python package source (8 modules)
├── tests/                             # pytest tests
├── .claude-plugin/
│   ├── plugin.json                    # Plugin manifest
│   └── marketplace.json               # Marketplace listing
├── .mcp.json                          # Plugin-bundled MCP registration
├── skills/
│   └── agent-sequencer/
│       ├── SKILL.md                   # Driving rules
│       ├── README.md                  # Skill details
│       ├── docs/
│       │   └── authoring-programs.md  # Program author guide
│       └── programs/                  # Bundled sequencer programs
│           ├── review_rounds.py
│           └── review_rounds/         # Self-contained bundle
│               ├── agents/            # python-sensei / sequencer-sensei / prompt-sensei
│               ├── scripts/
│               └── skills/            # sequencer-review / -respond / -resolve
└── .github/workflows/
    └── ci.yml                         # pytest + git install verification
```

---

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `AGENT_SEQUENCER_PROGRAMS_DIR` | Additional program search path (highest priority) | (unset) |
| `AGENT_SEQUENCER_STATE_DIR` | Directory for JSONL event logs | `~/.claude/sequencer/state/` |

Programs are searched in the following order (first match wins):

1. `$AGENT_SEQUENCER_PROGRAMS_DIR`
2. `<cwd>/.claude/sequencer/programs/`
3. `~/.claude/sequencer/programs/`

---

## Limitations (v1)

- The feedback re-fix loop (review-respond → review-resolve, repeated up to 3 times) is not yet implemented.
- `ParallelInstructions` (in-program fan-out declarations) is not yet implemented.
- HTTP/SSE transport (sharing across multiple Claude Code sessions) is not yet implemented.
- Program sandboxing (stronger trust boundary) is not yet implemented.
- Execution of TypeScript / Lua programs is not yet implemented.
- PyPI publishing is not supported (planned for Phase 2; only git-based distribution is available at this time).

---

## License

[MIT License](LICENSE) © 2026 OPENSPHERE Inc.
