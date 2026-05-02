# review_rounds — Review-and-fix bundle for sequencer programs

This is an **official feature bundle** that runs a parallel review of an
agent-sequencer sequencer program (and surrounding Python code) using
three expert agents, then iterates triage -> estimate -> fix ->
verification for up to N rounds until convergence.

By running their own sequencer programs through `review-rounds`, users
can have them reviewed against agent-sequencer best practices.

## Self-contained distribution

`review_rounds.py` depends only on the adjacent `review_rounds/`
directory. Copy the program and the directory together and the same
review functionality works in a different project (this bundle also
serves as a portability sample).

## Three experts

A sequencer program is a composite of **a Python generator + prompts +
the sequencer API**, so the review is split along three axes and run
in parallel:

| Expert | Focus |
|---|---|
| **python-sensei** | Python language semantics, type hints, async/await, PEP compliance, mutable default arguments, and other language-specific pitfalls |
| **sequencer-sensei** | The agent-sequencer API (Instruction / Done / Abort / Context), determinism, generator bidirectional communication, lifecycle, `expect_schema` design, and bundling |
| **prompt-sensei** | Structure of Instruction.text, consistency with `expect_schema`, explicit constraints to prevent runaway behavior, template design, minimal decoration |

## Layout

```
review_rounds.py                       — sequencer program body
review_rounds/
├── README.md                           — this file
├── skills/                             — skill definitions referenced from Instruction text
│   ├── sequencer-review.md             — parallel review by the three experts
│   ├── sequencer-review-respond.md     — triage -> estimate -> fix (with assignee dispatch)
│   └── sequencer-review-resolve.md     — verify the fixes
├── agents/
│   ├── python-sensei.md                — agent definition template
│   ├── sequencer-sensei.md             — same
│   └── prompt-sensei.md                — same
└── scripts/
    ├── fetch-diff.sh                   — fetch git diff
    ├── rm-tmp.sh                       — safe deletion under .claude/tmp/
    └── render-review.py                — apply events.jsonl into the review document
```

## Usage

When launching with `sequencer_start`, specify the review target and
related options in `params`:

```jsonc
{
  "program": "review-rounds",
  "params": {
    "max_rounds": 3,                    // default 5
    "base": "main",                     // default: agent resolves
    "target": "src/my_program",         // omit to review the entire diff
    "confirm": true,                    // default true (wait for user confirmation after estimate)
    "output_base": ".claude/tmp"        // default .claude/tmp
  }
}
```

### Example 1: Review agent-sequencer itself (when running inside the agent-sequencer repository)

```jsonc
"params": {
  "target": "src/agent_sequencer",
  "base": "main"
}
```

### Example 2: Review a specific user program

```jsonc
"params": {
  "target": "src/my_workflow.py",
  "max_rounds": 2,
  "confirm": false
}
```

### Example 3: Review the entire branch diff (omit target)

```jsonc
"params": {
  "base": "main"
}
```

## Parameter details

| Parameter | Default | Description |
|---|---|---|
| `max_rounds` | `5` | Maximum number of rounds for the outer loop (1-10) |
| `base` | resolved by agent | Base branch for the git diff being reviewed |
| `target` | (unset) | Review target path. When set, only files under that path are flagged |
| `output_base` | `.claude/tmp` | Output base directory for the review document |
| `confirm` | `true` | Enable `--confirm` for `sequencer-review-respond` (wait for user confirmation after estimate) |

## Convergence (decided deterministically by the program)

| Condition | Result |
|---|---|
| `findings_total == 0` | `Done(reason="Converged with zero findings")` |
| `code_changed == False` | `Done(reason="Converged with no code changes")` |
| `max_rounds` reached | `Abort(reason="...")` |

## Separation of concerns

- **Skills** (`sequencer-review*.md`) are generic tools: they accept
  options like `--target` / `--confirm`, but their defaults are neutral.
- **The program** (`review_rounds.py`) explicitly passes `target` /
  `confirm` in the Instruction text and decides the behavior for this
  specific use case (three sensei experts for sequencer programs,
  confirmation enabled by default, etc.).

If you want to review a different target or use a different expert
lineup, copy `review_rounds.py` into a new program — the
`sequencer-review*` skills can be reused as-is.

## Frontmatter naming convention

Skill / agent `name` fields **cannot contain symbols other than
hyphens**, so we use simple IDs that match the file name exactly:

| File | name |
|---|---|
| `skills/sequencer-review.md` | `sequencer-review` |
| `skills/sequencer-review-respond.md` | `sequencer-review-respond` |
| `skills/sequencer-review-resolve.md` | `sequencer-review-resolve` |
| `agents/python-sensei.md` | `python-sensei` |
| `agents/sequencer-sensei.md` | `sequencer-sensei` |
| `agents/prompt-sensei.md` | `prompt-sensei` |

## How the agents are invoked

The three sensei agents are not registered with Claude Code. At runtime
we launch `subagent_type=general-purpose` subagents and have each one
load the corresponding `agents/<name>.md` as context, which gives them
the expert role.

This means:
- No additional registration on the Claude Code side.
- The bundle works the moment it is copied wherever you need it.
- The user's settings are not polluted.
