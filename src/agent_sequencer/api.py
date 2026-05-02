"""Yield types produced by sequencer programs and the context passed to them.

Program authors import the types they need from this module:

  from agent_sequencer.api import Instruction, Done, Abort

See documents/agent-sequencer/implementation-plan.md section 5 for design
details.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


# ----------------------------------------------------------------------
# Yield types
# ----------------------------------------------------------------------
ON_INVALID_VALUES: tuple[str, ...] = ("retry", "abort")


@dataclass(frozen=True)
class Instruction:
    """Yield type representing an instruction to the agent.

    Attributes:
        text:            The instruction the agent must execute (natural
                         language).
        expect_schema:   JSON Schema the agent's result must satisfy. None
                         disables validation. Validation is performed by the
                         jsonschema library.
        on_invalid:      Behavior when the schema is violated.
                         "retry": Reissue the same instruction with a new
                         step_no (last_yield includes validation_error).
                         "abort": Terminate the instance with Abort.
        timeout_minutes: Maximum time to wait for the agent's response.
                         Observational metadata only.
    """

    text: str
    expect_schema: dict[str, Any] | None = None
    on_invalid: str = "retry"
    timeout_minutes: int | None = None

    def __post_init__(self) -> None:
        # Reject invalid on_invalid values at yield time so program-authoring
        # mistakes surface early. The Driver catches the ValueError and ends
        # the instance in failure.
        if self.on_invalid not in ON_INVALID_VALUES:
            raise ValueError(
                f"on_invalid must be one of {ON_INVALID_VALUES}: "
                f"got '{self.on_invalid}'"
            )


@dataclass(frozen=True)
class Done:
    """Yield type indicating successful program completion.

    Attributes:
        summary: Summary suitable for the final report to the user.
    """

    summary: dict[str, Any] | None = None


@dataclass(frozen=True)
class Abort:
    """Yield type indicating abnormal program termination.

    Attributes:
        reason: Reason for the abort (shown to the user).
    """

    reason: str


# ----------------------------------------------------------------------
# Progress hint (not a yield type; updated via ctx.publish_progress)
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Progress:
    """Progress hint published by the program via ctx.publish_progress(...).

    Surfaces in the sequencer_current response as progress_hint.
    Internal state used for program branching must not be exposed here
    (see implementation plan § 4.2).
    """

    current: int
    of: int
    label: str | None = None


# ----------------------------------------------------------------------
# Program runtime exceptions
# ----------------------------------------------------------------------
class StepFailed(Exception):
    """Exception delivered to the program via gen.throw on agent tool failure.

    The program can catch this with try/except and yield a recovery step:

        try:
            result = yield Instruction(...)
        except StepFailed as e:
            yield Instruction(text=f"Recovery: {e.reason}", ...)
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ----------------------------------------------------------------------
# Program execution context
# ----------------------------------------------------------------------
@dataclass
class Context:
    """Runtime context passed to run(ctx).

    Attributes:
        params: The params dict supplied to sequencer_start.
        env:    Environment metadata visible to the program (intended to be
                read-only).
    """

    params: dict[str, Any]
    env: dict[str, Any] = field(default_factory=dict)
    _on_progress: Callable[[Progress], None] | None = field(default=None, repr=False)

    def publish_progress(
        self, current: int, of: int, label: str | None = None
    ) -> None:
        """Publish a progress hint, surfaced in the sequencer_current response.

        Do not use this for program branching logic (see implementation plan
        § 4.2). Only expose the high-level progress overview meant for
        external observation.
        """
        if self._on_progress is not None:
            self._on_progress(Progress(current=current, of=of, label=label))
