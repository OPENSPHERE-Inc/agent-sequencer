# Sequencer Program Author's Guide

*[日本語版](authoring-programs_ja.md)*

This document is a guide for anyone creating or modifying **sequencer programs**
(classical programs written as Python generators) for agent-sequencer.

## 1. Philosophy and division of responsibilities

A sequencer program is a "**classical program for driving an AI agent**."

| Responsible party | Role |
|---|---|
| Program (the Python you write) | Control flow, decision logic, state transitions, convergence checks, aggregation |
| AI agent | Executes the `Instruction.text` yielded by the program and reports the result as JSON |
| Driver / runtime | Schema validation, step_no management, generator driving, JSONL persistence, replay |

**Decisions stay inside the program; the agent acts purely as a driver that executes instructions** —
this is the basic principle. Inside the program you write branches like `if result["x"] == ...:`,
and to the agent you simply say "do this."

## 2. Basic program structure

A minimal program consists of these four elements:

```python
from agent_sequencer.api import Done, Instruction

NAME = "hello"
DESCRIPTION = "One-line description of what the program does"
PARAMS_SCHEMA = {
    "name": {"type": "string", "default": "world"},
}

def run(ctx):
    result = yield Instruction(
        text=f"Please greet '{ctx.params.get('name', 'world')}'. "
             "Return JSON in the form {\"message\": \"...\"}.",
        expect_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    )
    yield Done(summary={"echo": result["message"]})
```

| Name | Required | Description |
|---|:-:|---|
| `NAME` | yes | Program name (the ID specified in `sequencer_start program=...`) |
| `DESCRIPTION` | – | Description shown by `sequencer_list_programs` |
| `PARAMS_SCHEMA` | – | Description of parameters that can be passed via `sequencer_start params={...}` |
| `run(ctx)` | yes | The generator function. `ctx` is the runtime context |

## 3. Where to place your program

Drop your `.py` file into one of the search-path directories below. The MCP server
scans them at startup (and on every `--watch` rescan), and registers each program
under its `NAME = "..."` constant.

### Search-path priority (first-match wins)

| Order | Path | Purpose |
|---|---|---|
| 1 | `$AGENT_SEQUENCER_PROGRAMS_DIR` | Environment-variable override (used by plugins to expose their bundled programs; also handy as a development override) |
| 2 | `<cwd>/.claude/sequencer/programs/` | Project-specific programs (commit them alongside the project that uses them) |
| 3 | `~/.claude/sequencer/programs/` | User-wide programs (available across every project you open) |

If two paths contain a program with the same `NAME`, the higher-priority one wins
and the lower-priority duplicate is silently shadowed.

### Choosing the right location

| Scenario | Recommended location |
|---|---|
| Workflow specific to one project (e.g. a release-prep checklist for repo X) | `<repo>/.claude/sequencer/programs/<your_program>.py` — commit it with the project |
| Personal helper you want available everywhere | `~/.claude/sequencer/programs/<your_program>.py` |
| Program you intend to publish for others | Bundle it inside your own Claude Code plugin (see [§ 10 Bundling programs](#10-bundling-programs-recommended)) and expose its directory via `AGENT_SEQUENCER_PROGRAMS_DIR` in the plugin's `.mcp.json` |

### Filename conventions

- File name: `snake_case.py`. Files starting with `_` are skipped.
- Each file declares `NAME = "kebab-case-name"` — that is the ID used in
  `sequencer_start program="..."`.
- Programs are scanned **only at the top level** of each search-path directory;
  subdirectories are ignored, so an adjacent `<program-name>/` bundle does not collide
  with the program file (see § 10).

## 4. Generator basics

We use Python generators' **bidirectional communication**. A `yield` expression both emits a value
and receives one.

```python
def run(ctx):
    result = yield Instruction(text="...", expect_schema={...})
    #   ↑                ↑
    #   |                Instruction sent to the agent
    #   |
    #   Receives the validated JSON result from the agent
```

The runtime `Driver` operates as follows:
1. Advance to the first yield with `next(gen)`.
2. Send the yielded `Instruction` to the agent.
3. Validate the agent's returned result against `expect_schema`.
4. Inject the validated result into the left-hand side of `result =` with `gen.send(validated_result)` and advance to the next yield.

### Yield types

| Type | Purpose |
|---|---|
| `Instruction(text, expect_schema, on_invalid, timeout_minutes)` | Instruction to the agent |
| `Done(summary)` | Normal program completion. `summary` is used in the final report to the user |
| `Abort(reason)` | Abnormal program termination. `reason` is shown to the user |

A bare `return` is also treated as completion via `StopIteration`, but **writing an explicit `yield Done()`
is recommended** (for observability and to make tracking via JSONL logs easier).

## 5. Designing Instructions

### 5.1 Writing `text`

This is the instruction text the agent reads. **Because it is meant for an AI to read**, you may
minimize Markdown decoration such as headings and blank-line paragraphs (see `review_rounds.py`).
Instead, focus on:

- **Reference the skill / procedure to use** at the very beginning (`Skill: <path>`)
- **State the target scope explicitly** (ambiguity causes the agent to overreach)
- **Show the report format (JSON) inline** as an example
- **State what must not be done** (e.g., consulting other context for understanding is allowed, but commenting on out-of-scope items is forbidden)

### 5.2 `expect_schema`

Strictly define **the shape the agent must return** with JSON Schema (`jsonschema>=4.0`).

```python
expect_schema = {
    "type": "object",
    "properties": {
        "findings_total": {"type": "integer", "minimum": 0},
        "doc_path": {"type": "string", "minLength": 1},
    },
    "required": ["findings_total", "doc_path"],
    "additionalProperties": True,  # Allow the agent to include extra information
}
```

**Key points**:
- Always specify `required` (so missing required fields are detected).
- Use `minimum` / `maximum` for numeric types and `minLength` / `enum` for strings.
- Set `additionalProperties: True` explicitly (let the agent include reasoning, statistics, etc.,
  while you only consume the fields you actually need).

### 5.3 `on_invalid` strategies

You can choose what happens on a schema violation:

| Value | Behavior |
|---|---|
| `"retry"` (default) | Re-issue the same Instruction with a new step_no. The violation is communicated to the agent via `last_yield.validation_error`. |
| `"abort"` | Abort the instance. |

`"retry"` is usually sufficient. Rather than writing program-side fallback logic that "switches behavior when an invalid value arrives,"
it is more robust to constrain via schema and let the agent fix it.

### 5.4 `timeout_minutes`

A guideline duration for the agent. The Driver does not forcibly interrupt, but the value is exposed
in `current.last_yield`, so it helps the agent understand "this is the kind of work that takes about this long."

## 6. Using the Context (`ctx`)

```python
def run(ctx):
    # Get parameters
    target = ctx.params.get("target", "default-value")

    # Publish a progress hint (observation only; do not use for decisions)
    ctx.publish_progress(current=1, of=10, label="Round 1/10")

    # ...
```

| Attribute / method | Purpose |
|---|---|
| `ctx.params` | The `params` dict passed to `sequencer_start` |
| `ctx.env` | Runtime metadata (intended read-only; mostly unused at present) |
| `ctx.publish_progress(current, of, label)` | Progress hint. Appears as `progress_hint` in the response from `sequencer_current` |

## 7. Determinism constraints (most important)

**Programs must be written deterministically.** This is a prerequisite for replaying JSONL logs (resume)
and arriving at the exact same final state.

### 7.1 What you must not do

- Use `time.time()` / `datetime.now()` / `random.*` directly.
- Perform file I/O, HTTP, or DB access directly inside the program.
- Mutate global state.
- Spawn external processes.

When you need any of these, **delegate to the agent through an Instruction**:

```python
# NG: taking the time inside the program
timestamp = datetime.now().isoformat()  # value would change on replay

# OK: ask the agent
result = yield Instruction(
    text="Please report the current time in ISO 8601 format: {\"timestamp\": \"...\"}",
    expect_schema={"type": "object", "required": ["timestamp"]},
)
timestamp = result["timestamp"]  # recorded in JSONL; identical on replay
```

### 7.2 Don't default with `or`

```python
# NG: also treats [], 0, and "" as "missing"
names = ctx.params.get("names") or ["world"]

# OK: fall back only when the key itself is missing
names = ctx.params.get("names", ["world"])
```

### 7.3 Aggregate with pure computation

```python
total_fixed = 0
for round_num in range(1, max_rounds + 1):
    ...
    total_fixed += result["fixed_count"]  # OK: pure computation from inputs
```

## 8. Program lifecycle

```
RUNNING → AWAITING_RESULT → (Done | Abort | exception) → TERMINAL_QUERYABLE
                                                            ↓
                                            (close / TTL / server stop)
                                                            ↓
                                                       ARCHIVED → PRUNED
```

- **Done**: normal completion. `summary` can be reported to the user.
- **Abort**: abnormal but anticipated termination. `reason` is shown to the user.
- **Exception**: unanticipated bug. The state becomes `failed` and an `error` kind `last_yield` is automatically returned to the agent.

## 9. Hot-reload caveats

Starting with `agent-sequencer --watch` enables automatic reload on changes to `programs/*.py`
(registry rescan).

### Don't put heavy initialization at module level

In `--watch` mode, every rescan re-`compile()`s and `exec()`s the module.
Heavy work at module level (connecting to external servers, reading large files, etc.) will
run on every reload.

```python
# NG: I/O at module load time
HEAVY_DATA = open("big.json").read()  # runs on every rescan

# OK: ask via Instruction inside run() when needed
def run(ctx):
    result = yield Instruction(text="Please read big.json and report its contents.", ...)
```

### Active instances are unaffected by reload

A running instance keeps the `run_fn` already captured by `Driver`, so even if you rewrite
the file mid-run, **that instance will finish on the old version**. The new version takes
effect from the next `sequencer_start`.

### Consistency check on resume

The JSONL `header.source_hash` is compared with the current source's hash; if they don't match,
resume is rejected with a `ProgramChanged` error. This is the mechanism for detecting
"the prerequisite for deterministic replay has been broken," and there is no way around it.

## 10. Bundling programs (recommended)

When a program depends on external skills, agent definitions, or scripts,
**collect those dependencies in an adjacent `<program-name>/` directory** so that the program
plus the directory can be carried to other projects as a single set.

Reference implementation: [`programs/review_rounds/`](../programs/review_rounds/README.md)

```
programs/
├── my_program.py            ← the sequencer program itself
└── my_program/              ← self-contained bundle
    ├── README.md
    ├── skills/
    │   └── <skill>.md       ← referenced from Instruction text
    ├── agents/
    │   └── <agent>.md       ← have general-purpose load this for context
    └── scripts/
        └── ...
```

### Notes

- Claude Code does **not register** `.md` files inside the bundle as skills or agents
  (only `.claude/skills/<name>/SKILL.md` and `.claude/agents/<name>.md` are registered).
  Bundle files are used by **referencing their paths** in Instruction text.
- The registry only scans **directly under** `programs/*.py`, so the `<program-name>/`
  subdirectory is ignored (no risk of collision).
- Modifying `.md` files inside the bundle does not change `source_hash` (the hash is taken
  only over the `.py` body of the program). If you split a template into a separate `.md`,
  loading it into memory at module load time works in practice, but the resume-time
  consistency check will not cover it (deferred to v2).
- Build paths to bundle files starting from `Path(__file__).parent / "<program-name>"`
  so they don't depend on the plugin install location (see the `_BUNDLE` constant in
  `review_rounds.py`).

## 11. Tips for writing prompts (Instruction.text)

Lessons learned from the `review_rounds.py` implementation:

### 11.1 Template constants + `format()`

Concatenated f-strings are less readable and maintainable than template constants built with
`textwrap.dedent` + triple-quoted strings:

```python
import textwrap

_TPL_REVIEW = textwrap.dedent("""\
    [Round {round_num}/{max_rounds} Step 1: {skill_name}]
    Skill: {skill_path}
    Target: {target}
    Report format (JSON): {{"result": <int>}}\
""")

# Caller side
text = _TPL_REVIEW.format(
    round_num=round_num,
    max_rounds=max_rounds,
    skill_name="python-review",
    skill_path=_PYTHON_REVIEW_SKILL,
    target=_TARGET,
)
```

The `{}` in JSON examples must be escaped as `{{}}`.

### 11.2 Minimize decoration

The prompt is read only by an AI, so `## h1` / `**bold**` / blank-line paragraphs are unnecessary.
A single `[Step name]` header line plus `key: value` lines is enough.

### 11.3 Separation of concerns

Skills should be general; the program should narrow the target. In `review_rounds.py`:

- `python-review.md` — a general-purpose Python review skill (scope is given via the `--target` argument)
- `review_rounds.py` — passes `--target .claude/skills/agent-sequencer/server` explicitly in the Instruction text

This keeps the python-review skill reusable from other programs.

## 12. How to test

A sequencer program can be unit-tested by **driving the Driver directly**, without going through
the MCP server:

```python
from pathlib import Path
from agent_sequencer.registry import ProgramRegistry
from agent_sequencer.runtime import Driver, KIND_DONE, KIND_ABORT

reg = ProgramRegistry([Path("programs").resolve()])
entry = reg.get("my-program")

driver = Driver(entry.run_fn, params={...})
driver.start()

# Step 1: confirm the first Instruction was emitted
assert driver.last_yield["kind"] == "instruction"
assert "expected text" in driver.last_yield["text"]

# Submit a result and advance to the next step
driver.send({"key": "value"})
assert driver.last_yield["kind"] == "instruction"  # Step 2

# ...

# Verify the terminal state
driver.send({"final": "result"})
assert driver.last_yield["kind"] == KIND_DONE
assert driver.last_yield["summary"]["..."] == ...
```

Reference: see `tests/test_hello.py` and `tests/test_review_rounds.py` in the repository
for working examples of this pattern.

## 13. Pre-publication checklist

- [ ] `NAME` / `DESCRIPTION` / `PARAMS_SCHEMA` / `run` are all present
- [ ] Every Instruction has `expect_schema` set (at minimum, `required` is specified)
- [ ] The terminal state is an explicit `yield Done(summary=...)` or `yield Abort(reason=...)`
- [ ] `time.time()` / `random.*` / direct I/O are not used
- [ ] Defaults use `ctx.params.get(key, default)` rather than the `or` idiom
- [ ] No heavy initialization at module level
- [ ] If a bundle is needed, dependencies are collected in an adjacent `<program-name>/` directory
- [ ] All branches have been verified by driving with `Driver` directly
