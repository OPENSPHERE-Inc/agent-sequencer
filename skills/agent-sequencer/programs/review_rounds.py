"""Sequencer program that reviews and fixes other sequencer programs.

Runs a parallel review of any user-authored sequencer program (and
surrounding Python code) using three expert agents
(python-sensei / sequencer-sensei / prompt-sensei) and iterates the
triage -> estimate -> fix -> verification cycle for up to N rounds
until convergence.

Within each round, the three steps
sequencer-review -> sequencer-review-respond -> sequencer-review-resolve
are delegated to the agent as Instructions.

All referenced skills, agents, and scripts are bundled in the adjacent
``review_rounds/`` directory (this also serves as a self-contained
distribution sample). No external skills are required.

The review target is specified via the ``target`` parameter (when
omitted, the entire repository diff is reviewed). To review
agent-sequencer itself, pass ``target="src/agent_sequencer"`` (when
running inside the agent-sequencer repository).

Convergence is decided deterministically by the program:
  - sequencer-review reports findings_total == 0           -> Done (resolved)
  - sequencer-review-respond reports code_changed == False -> Done (no further fixes possible)
  - max_rounds reached                                     -> Abort (did not converge)
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from agent_sequencer.api import Abort, Done, Instruction

NAME = "review-rounds"
DESCRIPTION = (
    "Review the user's sequencer program with three experts "
    "(python-sensei / sequencer-sensei / prompt-sensei), then respond "
    "and verify, iterating up to N rounds until convergence."
)

_DEFAULT_MAX_ROUNDS = 5
_DEFAULT_OUTPUT_BASE = ".claude/tmp"
_DEFAULT_CONFIRM = True

PARAMS_SCHEMA = {
    "max_rounds": {
        "type": "integer",
        "default": _DEFAULT_MAX_ROUNDS,
        "minimum": 1,
        "maximum": 10,
        "description": "Maximum number of rounds for the outer loop",
    },
    "base": {
        "type": "string",
        "description": (
            "Base branch passed to sequencer-review. "
            "When omitted, the agent resolves it on its own."
        ),
    },
    "target": {
        "type": "string",
        "description": (
            "Review target path (e.g. .claude/skills/agent-sequencer/server). "
            "When omitted, the entire repository diff is reviewed."
        ),
    },
    "output_base": {
        "type": "string",
        "default": _DEFAULT_OUTPUT_BASE,
        "description": "Output base directory for review documents",
    },
    "confirm": {
        "type": "boolean",
        "default": _DEFAULT_CONFIRM,
        "description": (
            "When True, wait for user confirmation immediately after the "
            "sequencer-review-respond estimate step (passes --confirm). "
            "When False, run the cycle straight through."
        ),
    },
}

# Path to the program bundle.
# The bundle is self-contained in the review_rounds/ directory adjacent to
# review_rounds.py, so resolving via __file__ yields an absolute path that
# does not depend on the plugin install location.
# Evaluated once at module load, so the result is deterministic
# (immutable within a single installation).
_BUNDLE = (Path(__file__).resolve().parent / "review_rounds").as_posix()

_REVIEW_SKILL = f"{_BUNDLE}/skills/sequencer-review.md"
_RESPOND_SKILL = f"{_BUNDLE}/skills/sequencer-review-respond.md"
_RESOLVE_SKILL = f"{_BUNDLE}/skills/sequencer-review-resolve.md"

_PYTHON_SENSEI = f"{_BUNDLE}/agents/python-sensei.md"
_SEQUENCER_SENSEI = f"{_BUNDLE}/agents/sequencer-sensei.md"
_PROMPT_SENSEI = f"{_BUNDLE}/agents/prompt-sensei.md"

# expect_schema definitions for each Instruction (collected at the top of the
# file so they are easy to follow during review).
_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "doc_path": {"type": "string", "minLength": 1},
        "findings_total": {"type": "integer", "minimum": 0},
    },
    "required": ["doc_path", "findings_total"],
    "additionalProperties": True,
}

_RESPOND_SCHEMA = {
    "type": "object",
    "properties": {
        "fixed_count": {"type": "integer", "minimum": 0},
        "wontfix_count": {"type": "integer", "minimum": 0},
        "code_changed": {"type": "boolean"},
    },
    "required": ["fixed_count", "wontfix_count", "code_changed"],
    "additionalProperties": True,
}

_RESOLVE_SCHEMA = {
    "type": "object",
    "properties": {
        "unresolved_count": {"type": "integer", "minimum": 0},
    },
    "required": ["unresolved_count"],
    "additionalProperties": True,
}

# ----------------------------------------------------------------------
# Instruction templates
# ----------------------------------------------------------------------
# Prompts are read only by the AI agent, so markdown decoration
# (h1/h2 headings, blank-line paragraphs) is kept to a minimum.
# However, in source the templates use textwrap.dedent + triple-quoted
# strings to keep them readable.
# Because format() is used for substitution, JSON sample braces are
# escaped as {{ }}.

_TPL_REVIEW = textwrap.dedent("""\
    [Round {round_num}/{max_rounds} Step 1: sequencer-review]
    Skill: {skill}
    Run a parallel sequencer-program code review against {base_clause}.
    {target_clause}
    Expert agents to use (launch all three in parallel):
    - python-sensei      ({python_sensei})
    - sequencer-sensei   ({sequencer_sensei})
    - prompt-sensei      ({prompt_sensei})
    Agent launch: spawn three subagent_type=general-purpose subagents in parallel and have each one Read the corresponding agents/<name>.md to take on its expert role.
    Output base path for the review document: {output_base}
    Response format (JSON): {{"doc_path": "<file path>", "findings_total": <int>}}\
""")

_TPL_RESPOND = textwrap.dedent("""\
    [Round {round_num}/{max_rounds} Step 2: sequencer-review-respond]
    Skill: {skill}
    Address the findings in review document {doc_path}.
    {confirm_clause}
    Experts to launch for triage / estimate / fix (one expert assigned per finding by triage):
    - python-sensei      ({python_sensei})
    - sequencer-sensei   ({sequencer_sensei})
    - prompt-sensei      ({prompt_sensei})
    Agent launch: subagent_type=general-purpose with the assigned agents/<name>.md loaded as context.
    Verification (Step 5): Python verification (compileall + import smoke / optionally ruff / optionally test_*.py).
    Response format (JSON): {{"fixed_count": <int>, "wontfix_count": <int>, "code_changed": <bool>}}
    - fixed_count: number of findings triaged as Will Fix and actually fixed.
    - wontfix_count: number of findings classified as Won't Fix or Downgrade.
    - code_changed: whether any line of source code in {target_label} was modified.\
""")

_TPL_RESOLVE = textwrap.dedent("""\
    [Round {round_num}/{max_rounds} Step 3: sequencer-review-resolve]
    Skill: {skill}
    Verify the fixes in review document {doc_path}.
    Response format (JSON): {{"unresolved_count": <int>}}
    - unresolved_count: number of findings whose Verification is still 💬 Feedback.\
""")


def _build_target_clauses(target: str | None) -> tuple[str, str]:
    """Return the Step 1 clause and Step 2 label based on whether target is set."""
    if target:
        review_clause = (
            f"Review target (restricted): only files under {target}/.\n"
            f"    - Pass {target} as the --target argument to the sequencer-review skill.\n"
            f"    - Even if the diff contains other files, do not raise findings outside {target}/ "
            f"(reading them for context is allowed)."
        )
        target_label = f"under {target}/"
    else:
        review_clause = (
            "Review target: the entire diff (do not pass --target).\n"
            "    - The caller did not specify a path, so every file in the branch diff is in scope."
        )
        target_label = "the entire repository"
    return review_clause, target_label


def run(ctx):
    """Sequencer program body that drives the round loop."""
    max_rounds = ctx.params.get("max_rounds", _DEFAULT_MAX_ROUNDS)
    base = ctx.params.get("base")
    target = ctx.params.get("target")
    output_base = ctx.params.get("output_base", _DEFAULT_OUTPUT_BASE)
    confirm = ctx.params.get("confirm", _DEFAULT_CONFIRM)

    base_clause = f"base branch {base}" if base else "the default base branch"
    target_clause, target_label = _build_target_clauses(target)
    confirm_clause = (
        "Option: enable --confirm (wait for user confirmation immediately after the estimate step)."
        if confirm
        else "Option: --confirm is disabled (run straight through after the estimate step)."
    )

    total_fixed = 0
    total_wontfix = 0
    last_unresolved = 0

    for round_num in range(1, max_rounds + 1):
        ctx.publish_progress(
            current=round_num,
            of=max_rounds,
            label=f"Round {round_num}/{max_rounds}",
        )

        # ----- Step 1: sequencer-review -----
        # The skill itself is generic; restricting the target is this program's job.
        review_result = yield Instruction(
            text=_TPL_REVIEW.format(
                round_num=round_num,
                max_rounds=max_rounds,
                skill=_REVIEW_SKILL,
                base_clause=base_clause,
                target_clause=target_clause,
                python_sensei=_PYTHON_SENSEI,
                sequencer_sensei=_SEQUENCER_SENSEI,
                prompt_sensei=_PROMPT_SENSEI,
                output_base=output_base,
            ),
            expect_schema=_REVIEW_SCHEMA,
            timeout_minutes=60,
        )

        if review_result["findings_total"] == 0:
            yield Done(
                summary={
                    "rounds_executed": round_num,
                    "converged": True,
                    "reason": "Converged with zero findings",
                    "total_fixed": total_fixed,
                    "total_wontfix": total_wontfix,
                    "last_unresolved": last_unresolved,
                }
            )
            return

        doc_path = review_result["doc_path"]

        # ----- Step 2: sequencer-review-respond -----
        respond_result = yield Instruction(
            text=_TPL_RESPOND.format(
                round_num=round_num,
                max_rounds=max_rounds,
                skill=_RESPOND_SKILL,
                doc_path=doc_path,
                confirm_clause=confirm_clause,
                python_sensei=_PYTHON_SENSEI,
                sequencer_sensei=_SEQUENCER_SENSEI,
                prompt_sensei=_PROMPT_SENSEI,
                target_label=target_label,
            ),
            expect_schema=_RESPOND_SCHEMA,
            timeout_minutes=120,
        )

        total_fixed += respond_result["fixed_count"]
        total_wontfix += respond_result["wontfix_count"]

        # ----- Step 3: sequencer-review-resolve -----
        resolve_result = yield Instruction(
            text=_TPL_RESOLVE.format(
                round_num=round_num,
                max_rounds=max_rounds,
                skill=_RESOLVE_SKILL,
                doc_path=doc_path,
            ),
            expect_schema=_RESOLVE_SCHEMA,
            timeout_minutes=30,
        )
        last_unresolved = resolve_result["unresolved_count"]

        # ----- Convergence check: no code changes means further rounds are pointless. -----
        if not respond_result["code_changed"]:
            yield Done(
                summary={
                    "rounds_executed": round_num,
                    "converged": True,
                    "reason": "Converged with no code changes",
                    "total_fixed": total_fixed,
                    "total_wontfix": total_wontfix,
                    "last_unresolved": last_unresolved,
                }
            )
            return

    # ----- max_rounds reached -----
    yield Abort(
        reason=(
            f"Reached the maximum of {max_rounds} rounds without converging. "
            f"Cumulative fixed={total_fixed}, wontfix={total_wontfix}, "
            f"unresolved in the final round={last_unresolved}. "
            "Increase max_rounds or review the remaining unresolved findings."
        )
    )
