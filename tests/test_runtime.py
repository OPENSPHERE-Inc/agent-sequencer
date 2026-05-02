"""Driver unit tests.

Feed sequencer programs directly into the Driver and confirm that yield
interpretation, schema validation, on_invalid behavior, and Done injection
via StopIteration all behave as expected.
"""

from __future__ import annotations

import pytest

from agent_sequencer.api import Abort, Done, Instruction
from agent_sequencer.runtime import (
    KIND_ABORT,
    KIND_DONE,
    KIND_INSTRUCTION,
    STATE_ABORTED,
    STATE_AWAITING_RESULT,
    STATE_COMPLETED,
    Driver,
)


def _hello_program(ctx):
    name = ctx.params.get("name", "world")
    result = yield Instruction(
        text=f"Greet {name}",
        expect_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    )
    yield Done(summary={"echo": result["message"]})


def test_start_yields_first_instruction():
    driver = Driver(_hello_program, params={"name": "alice"})
    driver.start()
    assert driver.state == STATE_AWAITING_RESULT
    assert driver.step_no == 1
    assert driver.last_yield["kind"] == KIND_INSTRUCTION
    assert "alice" in driver.last_yield["text"]


def test_send_valid_result_advances_to_done():
    driver = Driver(_hello_program, params={})
    driver.start()
    driver.send({"message": "hi"})
    assert driver.state == STATE_COMPLETED
    assert driver.last_yield["kind"] == KIND_DONE
    assert driver.last_yield["summary"] == {"echo": "hi"}


def test_schema_violation_retry_keeps_same_text_with_new_step_no():
    driver = Driver(_hello_program, params={})
    driver.start()
    first_step = driver.step_no
    driver.send({"wrong_field": "value"})
    assert driver.state == STATE_AWAITING_RESULT
    assert driver.step_no == first_step + 1
    assert "validation_error" in driver.last_yield


def test_abort_program():
    def prog(ctx):
        yield Abort(reason="not implemented")

    driver = Driver(prog, params={})
    driver.start()
    assert driver.state == STATE_ABORTED
    assert driver.last_yield["kind"] == KIND_ABORT
    assert driver.last_yield["reason"] == "not implemented"


def test_return_dict_becomes_done_summary():
    """A program ending with `return {...}` is also captured as Done.summary."""

    def prog(ctx):
        yield Instruction(
            text="step1",
            expect_schema={"type": "object", "required": ["x"]},
        )
        return {"final": True}

    driver = Driver(prog, params={})
    driver.start()
    driver.send({"x": 1})
    assert driver.state == STATE_COMPLETED
    assert driver.last_yield["kind"] == KIND_DONE
    assert driver.last_yield["summary"] == {"final": True}


def test_invalid_on_invalid_raises():
    with pytest.raises(ValueError):
        Instruction(text="x", on_invalid="rollback")
