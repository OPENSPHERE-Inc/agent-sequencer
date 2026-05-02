---
name: sequencer-review
description: Run a parallel code review of an agent-sequencer sequencer program (and surrounding Python code)
---

# sequencer-review

This is the skill used to review agent-sequencer sequencer programs.
It is referenced by the `review_rounds.py` program through Instruction
calls. It is **not** registered as a Claude Code skill; it is loaded by
**path reference** from ordinary tools such as Bash / Read / Write.

Three expert agents are launched in parallel and each contributes
findings from its own angle. The findings are merged into a single
review document.

## Role

You are the **review leader**. Your job is to launch the three experts
in parallel and merge each reviewer's findings into a single report.

## Round number

If the arguments include a round number (e.g. `Round 1`, `Round 2`),
reflect it in the report title.

## Input

The calling program is expected to specify the review target via
`--target <path>`. When omitted, the entire working tree (every
modified file in the diff) is the target.

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--base {branch}` | `main` or `master` | Specify the base branch |
| `--target {path}` | (entire diff if unset) | Review target path. When set, instruct each reviewer to raise findings only against files under that path |

If the base branch is not specified via `--base`, use `main` or
`master` from the remote (prefer `main` when both exist).

## Reviewers (three experts)

A sequencer program is a composite of a Python generator + prompts +
the sequencer API, so the review is split along three axes and each
expert is launched in parallel:

| Reviewer | Focus | Agent definition |
|----------|-------|------------------|
| **python-sensei** | Python language semantics, type hints, async/await, PEP compliance, mutable default arguments, and other language-specific pitfalls | `skills/agent-sequencer/programs/review_rounds/agents/python-sensei.md` |
| **sequencer-sensei** | Correct use of the agent-sequencer API, determinism, generator bidirectional communication, lifecycle, `expect_schema` design, bundling | `skills/agent-sequencer/programs/review_rounds/agents/sequencer-sensei.md` |
| **prompt-sensei** | Structure of Instruction.text, consistency with `expect_schema`, explicit constraints to prevent runaway behavior, template design, minimal decoration | `skills/agent-sequencer/programs/review_rounds/agents/prompt-sensei.md` |

> Operates on the assumption that the three custom agents above are
> **not** registered with Claude Code. In practice, `general-purpose`
> subagents are launched and the agent definition files above are
> loaded as context (see the template below).

## Step 1 — Identify review scope and fetch the diff

1. If `--target` is given, that path is the review target; otherwise the entire diff is.
2. Skim the target files with Read / Glob to understand the scope of changes.
3. **Fetch diff information via the script** (`fetch-diff.sh` outputs the entire diff, so when `--target` is set, the reviewer prompt in Step 2 must explicitly state the restricted scope):
   - Generate the output file path: `.claude/tmp/parallel-review-diff-{timestamp}.txt`
   - Run:
     ```
     bash skills/agent-sequencer/programs/review_rounds/scripts/fetch-diff.sh {base} {output-file-path}
     ```
4. **Decide how to hand the diff to the reviewers based on size:**
   - **Under 1,000 lines:** read the file and embed the contents directly in the reviewer prompt.
   - **1,000 lines or more:** pass the **file path** to the reviewers and have them Read it.

## Step 2 — Launch the three reviewers in parallel

Use the `Agent` tool to **simultaneously launch** three
**`general-purpose`** subagents (`subagent_type` is `general-purpose`).
Pass the path to the expert role definition into each subagent.

### Agent prompt template

Send the following to each reviewer (substitute `{specialist}` with
`python-sensei` / `sequencer-sensei` / `prompt-sensei`, and
`{agent_def}` with the corresponding `agents/<name>.md` path):

```
You are {specialist}. Read the expert role definition in the file
below and conduct the code review strictly according to it.

Agent definition: {agent_def}
(Read this file with the Read tool and adopt it as your role.)

Review target:
- Scope: {targets} (entire diff when --target is unset)
- Base branch: {base}
- **Do not raise findings against files outside the target scope.** The diff
  may contain files outside the target scope; ignore them (reading them for
  context is allowed).

Diff and context (already fetched by the review leader via the script):
{diff_content_or_path}
(Contents: changed-file list / commit log / commit diff / staged changes / unstaged changes)

Rules:
- This is read-only. Do not edit or write any file.
- **The diff has already been provided above. Do not re-run `git diff`,
  `git log`, or `git show`.** Use Read to examine surrounding code (lines
  before and after the change, callers, related files, etc.) when needed.
- For code investigation use only Read, Glob, Grep, and Bash (limited to grep, ls, find).
- Tag every finding with one of the following severity labels:
  - **Critical** — fatal / high risk (must fix)
  - **Major** — medium risk (should fix)
  - **Minor** — low risk / advisory
  - **Info** — informational / future reference
- **Do not raise findings outside your area of expertise** (python-sensei
  on agent-sequencer API matters, sequencer-sensei on prompt structure,
  prompt-sensei on Python language semantics, etc.) — leave those to the
  other experts.

Output findings as a numbered list in the following format:
[severity] file_path:line — description of the issue and why it matters.
```

## Step 3 — Merge the report

After the three reviewers finish, merge the findings into a single report:

1. **Deduplicate** — when multiple reviewers raise the same issue, collapse
   them into one entry and record which reviewers identified it.
2. **Sort** — group by severity: Critical -> Major -> Minor -> Info.

**Triage of whether to fix is not done in this skill** (that is the
responsibility of sequencer-review-respond).

### Report format

```markdown
# Parallel code review report — Round {N}

- **Date:** YYYY-MM-DD
- **Round:** {N}
- **Scope:** {description of the review target}
- **Reviewers:** python-sensei, sequencer-sensei, prompt-sensei

## Critical

### C-1 — `file.py:42`

- **Reviewer:** sequencer-sensei

**Finding:**

{description of the issue}

<!-- METADATA(C-1) -->
<!-- /METADATA(C-1) -->

---

## Major

### M-1 — `other.py:120`

- **Reviewer:** python-sensei, prompt-sensei

**Finding:**

{description of the issue}

<!-- METADATA(M-1) -->
<!-- /METADATA(M-1) -->

---

## Minor

No findings

---

## Info

No findings

---

## Summary

- **Critical:** N
- **Major:** N
- **Minor:** N
- **Info:** N
- **Total:** N findings from 3 reviewers (D duplicates merged)
```

### Formatting rules

- Each finding is its own subsection with a heading of the form `### {finding-id} — `{location}``.
- Record the metadata (reviewers) per finding as a bullet list, followed by the description under a bold "Finding" label.
- After the description, before the `---` separator, place metadata-insertion markers `<!-- METADATA({finding-id}) -->` and `<!-- /METADATA({finding-id}) -->` separated by blank lines. **Leave the space between the markers empty** (metadata is inserted later by the downstream tooling).
- Separate findings with a `---` horizontal rule. **Do not output a Status line** (that is outside this skill's responsibility).
- For severity sections with no findings (`## Critical` / `## Major` / `## Minor` / `## Info`), **still output the heading** and write `No findings` in the body.

## Step 4 — Clean up the temporary file

After the report is finalized, delete the diff file fetched in Step 1, item 3.

```bash
bash skills/agent-sequencer/programs/review_rounds/scripts/rm-tmp.sh {diff-file-path}
```
