# agent-sequencer

*[日本語版 README](README_ja.md)*

**An MCP skill + server that lets a Python script drive an AI agent through
strictly-defined workflows and long-running tasks.**

```
+--------------+    MCP tool call     +-------------------------------+
|              | -------------------> |       agent-sequencer         |
|   AI Agent   |                      |     (MCP stdio server)        |
| (Claude Code)| <------------------- |                               |
|              |    yield Instruction |  +-------------------------+  |
+------+-------+                      |  |   Sequencer Program     |  |
       |                              |  |   (Python generator)    |  |
       | step execution via own tools |  |   branching / control   |  |
       | (Bash / Edit / Skill / ...)  |  +-------------------------+  |
       v                              |                               |
     User                             |  +-------------------------+  |
                                      |  |   JSONL event log       |  |
                                      |  |   (deterministic replay)|  |
                                      |  +-------------------------+  |
                                      +-------------------------------+
```

## Architecture overview

- **Sequencer program**: a classical program written as a Python generator.
  Workflow branching, aggregation, and termination logic stay inside the program.
- **Step boundary**: each `yield Instruction(...)` in the program is one step.
  It declares an instruction text plus a JSON Schema for the response; the AI agent
  executes the instruction with its own tools (Bash / Edit / Skill / ...) and returns
  the result as JSON.
- **Deterministic replay**: every event is appended to a JSONL log; after a server
  restart, an interrupt, or a context compaction, the program is fully recoverable
  by re-running it from the start and re-injecting the recorded inputs.

Because guardrails live in **code** rather than in prompts, the system stays stable
as conversation context degrades over long-running tasks.

- **Supported editors**: Claude Code
- **Language / runtime**: Python ≥ 3.11
- **Distribution**: Claude Code plugin (git-based)
- **License**: MIT

---

## Features

- **Author your own sequencer program in Python** — keep workflow branching, aggregation,
  and termination logic in code, and delegate per-step execution to the AI agent.
  Author's guide: [`docs/authoring-programs.md`](skills/agent-sequencer/docs/authoring-programs.md).
- **Invoke it from an AI agent (Claude Code) via MCP tools** — `sequencer_list_programs`
  to discover, `sequencer_start` to launch, `sequencer_next` to submit a result,
  `sequencer_resume` to recover an interrupted instance.
- **Stable execution of long-running workflows** — every step's response is validated
  against a JSON Schema with automatic retry on violation; interruptions and post-compact
  desyncs recover via deterministic replay of a JSONL event log; `--watch` hot-reloads
  program edits during development.

For details, see [`skills/agent-sequencer/SKILL.md`](skills/agent-sequencer/SKILL.md) (driving rules) and
[`skills/agent-sequencer/docs/authoring-programs.md`](skills/agent-sequencer/docs/authoring-programs.md) (program author's guide).

For your own programs, the bundled
[`review-rounds`](skills/agent-sequencer/programs/review_rounds/README.md)
program (three specialist agents review → fix → verify in parallel) is available as a
self-review helper — and as a sample implementation to crib from.

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

For a first run, the bundled `hello` program (a minimal sample / smoke test) is the
quickest way to verify the wiring.

### A. Specify a program by name (smoke test)

```
Start the hello program with agent-sequencer
(names=["Alice", "Bob"])
```

The agent looks the program up via `sequencer_list_programs`, calls
`sequencer_start program="hello" params={"names": ["Alice", "Bob"]}`, generates a one-line
greeting per name, submits each result with `sequencer_next`, and finally calls
`sequencer_close`.

### B. Describe what you want (your own program)

```
Run my-workflow with agent-sequencer
```

The agent picks the matching program (e.g. one you placed under
`<cwd>/.claude/sequencer/programs/my_workflow.py`) and starts it.

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
