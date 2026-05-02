"""Generator driver.

Drives a sequencer program (a Python generator), interprets the values it
yields, and updates state. Mediates instruction delivery to the agent and
result injection back into the program.

  - start():  Start the generator and advance to the first yield.
  - send():   Validate the agent's result; on success, inject it via
              gen.send and advance to the next yield. On schema violation,
              apply the on_invalid strategy (retry / abort).
  - throw():  Deliver an exception to the program via gen.throw (used from
              step 3 onward).

Step 3 implemented JSON Schema validation via expect_schema and the
on_invalid strategies (retry / abort).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import jsonschema

from .api import Abort, Context, Done, Instruction, Progress

logger = logging.getLogger(__name__)


# State strings (the `state` field returned by sequencer_current /
# sequencer_next).
STATE_AWAITING_RESULT = "awaiting_result"
STATE_COMPLETED = "completed"
STATE_ABORTED = "aborted"
STATE_FAILED = "failed"

# Values for last_yield.kind
KIND_INSTRUCTION = "instruction"
KIND_DONE = "done"
KIND_ABORT = "abort"
KIND_ERROR = "error"


class Driver:
    """Owns startup and progression of the generator.

    One driver per instance. Not thread-safe (mutual exclusion is provided
    at the instance layer in step 4).
    """

    def __init__(self, run_fn: Any, params: dict[str, Any]):
        self._progress: Progress | None = None
        ctx = Context(params=params, _on_progress=self._set_progress)
        # Build the generator. It has not been advanced yet: just after
        # __init__ the first yield has not been reached.
        self._gen = run_fn(ctx)
        # The Instruction currently awaiting an agent result. Referenced
        # during send-time schema validation and when reissuing on retry.
        self._pending_instruction: Instruction | None = None

        # Public state
        self.step_no: int = 0
        self.state: str = STATE_AWAITING_RESULT  # tentative; finalized in start()
        self.last_yield: dict[str, Any] | None = None
        self.yielded_at: datetime | None = None
        # Time of transition to a terminal state. Used for the
        # TERMINAL_QUERYABLE TTL check.
        self.terminal_at: datetime | None = None

    # ------------------------------------------------------------------
    # Progress hint
    # ------------------------------------------------------------------
    def _set_progress(self, progress: Progress) -> None:
        self._progress = progress

    @property
    def progress_hint(self) -> dict[str, Any] | None:
        """Value placed in the progress_hint field of sequencer_current."""
        if self._progress is None:
            return None
        return {
            "current": self._progress.current,
            "of": self._progress.of,
            "label": self._progress.label,
        }

    # ------------------------------------------------------------------
    # Generator driving
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start the generator and advance to the first yield."""
        self._advance(send_value=None, send=False)

    def send(self, result: Any) -> None:
        """Validate the agent's result, inject it, and advance to the next yield.

        When expect_schema is violated, behave per on_invalid:
          - "retry": Reissue the same Instruction with a new step_no
                     (last_yield carries validation_error). The generator is
                     not advanced.
          - "abort": Terminate the instance with Abort.
        """
        pending = self._pending_instruction
        if pending is None:
            # send() was called when the driver was not awaiting a result.
            # Normally tools.py rejects this via is_terminal(); this is a
            # defensive guard.
            self._set_terminal_failed(
                RuntimeError("Driver is not awaiting a result")
            )
            return

        # Schema validation (only when expect_schema is provided).
        if pending.expect_schema is not None:
            try:
                jsonschema.validate(
                    instance=result, schema=pending.expect_schema
                )
            except jsonschema.ValidationError as e:
                self._handle_invalid_result(pending, e.message)
                return
            except jsonschema.SchemaError as e:
                # expect_schema itself is invalid - treat as a program
                # authoring mistake and end the instance in failure.
                self._set_terminal_failed(
                    ValueError(
                        f"Program's expect_schema is not a valid JSON Schema: "
                        f"{e.message}"
                    )
                )
                return

        # Validation passed - inject into the generator and advance to the
        # next yield.
        self._advance(send_value=result, send=True)

    def throw(self, exc: BaseException) -> None:
        """Inject an exception into the program (used from step 3 onward)."""
        try:
            yielded = self._gen.throw(exc)
        except StopIteration as stop:
            summary = stop.value if isinstance(stop.value, dict) else {}
            self._set_terminal_done(summary)
            return
        except Exception as e:
            self._set_terminal_failed(e)
            return
        self._handle_yield(yielded)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _advance(self, send_value: Any, send: bool) -> None:
        """Advance the generator by one step.

        send=False: next(gen)  - used by start.
        send=True : gen.send(send_value) - used to inject a result.
        """
        try:
            if send:
                yielded = self._gen.send(send_value)
            else:
                yielded = next(self._gen)
        except StopIteration as stop:
            # The program ended with `return` (completed without an explicit
            # Done). If the return value is a dict, adopt it as the summary.
            summary = stop.value if isinstance(stop.value, dict) else {}
            self._set_terminal_done(summary)
            return
        except Exception as e:
            # Exception raised inside the program - fail the instance.
            logger.exception("Exception raised while running program: %s", e)
            self._set_terminal_failed(e)
            return
        self._handle_yield(yielded)

    def _handle_yield(self, yielded: Any) -> None:
        """Interpret a yielded value and update state accordingly."""
        if isinstance(yielded, Instruction):
            self._set_instruction(yielded)
        elif isinstance(yielded, Done):
            self._set_terminal_done(yielded.summary or {})
        elif isinstance(yielded, Abort):
            self._set_terminal_abort(yielded.reason)
        else:
            # Unknown yield type - treat as a program bug and fail.
            self._set_terminal_failed(
                TypeError(
                    f"Unexpected yield type: {type(yielded).__name__}. "
                    "Yield Instruction, Done, or Abort."
                )
            )

    def _handle_invalid_result(
        self, instr: Instruction, validation_error: str
    ) -> None:
        """Apply the on_invalid strategy when the result fails schema validation.

        on_invalid is validated in Instruction.__post_init__, so only
        "retry" and "abort" need to be handled here.
        """
        logger.info(
            "Schema violation (step_no=%d, on_invalid=%s): %s",
            self.step_no,
            instr.on_invalid,
            validation_error,
        )
        if instr.on_invalid == "abort":
            self._set_terminal_abort(
                f"Aborted due to schema violation: {validation_error}"
            )
        else:  # "retry"
            self._reissue_instruction(instr, validation_error=validation_error)

    def _reissue_instruction(
        self, instr: Instruction, validation_error: str
    ) -> None:
        """Reissue the same Instruction with a new step_no (retry strategy).

        The generator is not advanced (the pending instruction stays the
        same). validation_error is attached to last_yield so the agent can
        recognize this as a re-request triggered by the previous result
        being invalid.
        """
        self.step_no += 1
        self.state = STATE_AWAITING_RESULT
        # _pending_instruction keeps holding the same instr (used for revalidation).
        self._pending_instruction = instr
        self.last_yield = {
            "kind": KIND_INSTRUCTION,
            "step_no": self.step_no,
            "text": instr.text,
            "expect_schema": instr.expect_schema,
            "on_invalid": instr.on_invalid,
            "timeout_minutes": instr.timeout_minutes,
            "validation_error": validation_error,
        }
        self.yielded_at = datetime.now(timezone.utc)

    def _set_instruction(self, instr: Instruction) -> None:
        # step_no increments by 1 on each issuance (implementation plan § 9.1).
        self.step_no += 1
        self.state = STATE_AWAITING_RESULT
        self._pending_instruction = instr
        self.last_yield = {
            "kind": KIND_INSTRUCTION,
            "step_no": self.step_no,
            "text": instr.text,
            "expect_schema": instr.expect_schema,
            "on_invalid": instr.on_invalid,
            "timeout_minutes": instr.timeout_minutes,
        }
        self.yielded_at = datetime.now(timezone.utc)

    def _set_terminal_done(self, summary: dict[str, Any]) -> None:
        self._pending_instruction = None
        self.state = STATE_COMPLETED
        self.last_yield = {
            "kind": KIND_DONE,
            "step_no": self.step_no,
            "summary": summary,
        }
        now = datetime.now(timezone.utc)
        self.yielded_at = now
        self.terminal_at = now

    def _set_terminal_abort(self, reason: str) -> None:
        self._pending_instruction = None
        self.state = STATE_ABORTED
        self.last_yield = {
            "kind": KIND_ABORT,
            "step_no": self.step_no,
            "reason": reason,
        }
        now = datetime.now(timezone.utc)
        self.yielded_at = now
        self.terminal_at = now

    def _set_terminal_failed(self, exc: BaseException) -> None:
        self._pending_instruction = None
        self.state = STATE_FAILED
        self.last_yield = {
            "kind": KIND_ERROR,
            "step_no": self.step_no,
            "type": type(exc).__name__,
            "message": str(exc),
        }
        now = datetime.now(timezone.utc)
        self.yielded_at = now
        self.terminal_at = now

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------
    def is_terminal(self) -> bool:
        """Whether the driver is in a terminal state (completed / aborted / failed)."""
        return self.state in (STATE_COMPLETED, STATE_ABORTED, STATE_FAILED)
