"""Drive the bundled `hello` sample program through Driver."""

from __future__ import annotations

import pytest

from agent_sequencer.registry import ProgramRegistry
from agent_sequencer.runtime import (
    KIND_ABORT,
    KIND_DONE,
    KIND_INSTRUCTION,
    Driver,
)


@pytest.fixture(scope="module")
def hello_run_fn(bundled_programs_dir):
    if not bundled_programs_dir.exists():
        pytest.skip("bundled programs directory not present")
    reg = ProgramRegistry([bundled_programs_dir])
    entry = reg.get("hello")
    if entry is None:
        pytest.skip("hello program not loaded")
    return entry.run_fn


def _make_driver(run_fn, **params):
    driver = Driver(run_fn, params=params)
    driver.start()
    return driver


def test_default_greets_world(hello_run_fn):
    driver = _make_driver(hello_run_fn)
    assert driver.last_yield["kind"] == KIND_INSTRUCTION
    assert "world" in driver.last_yield["text"]
    driver.send({"message": "Hello, world!"})
    assert driver.last_yield["kind"] == KIND_DONE
    summary = driver.last_yield["summary"]
    assert summary["greeted_count"] == 1
    assert summary["greetings"] == [{"name": "world", "message": "Hello, world!"}]


def test_greets_multiple_names_in_order(hello_run_fn):
    driver = _make_driver(hello_run_fn, names=["Alice", "Bob"])
    # Step 1: Alice
    assert "Alice" in driver.last_yield["text"]
    driver.send({"message": "Hi Alice"})
    # Step 2: Bob
    assert driver.last_yield["kind"] == KIND_INSTRUCTION
    assert "Bob" in driver.last_yield["text"]
    driver.send({"message": "Hi Bob"})
    # Done
    assert driver.last_yield["kind"] == KIND_DONE
    summary = driver.last_yield["summary"]
    assert summary["greeted_count"] == 2
    assert [g["name"] for g in summary["greetings"]] == ["Alice", "Bob"]


def test_empty_list_aborts(hello_run_fn):
    """An explicitly empty `names` list aborts (param schema enforces this too,
    but the program guards defensively)."""
    driver = _make_driver(hello_run_fn, names=[])
    assert driver.last_yield["kind"] == KIND_ABORT
    assert "names" in driver.last_yield["reason"].lower()


def test_schema_violation_retries(hello_run_fn):
    driver = _make_driver(hello_run_fn, names=["Carol"])
    initial_step = driver.step_no
    driver.send({"wrong_field": "oops"})
    # Retry: same instruction text, new step number
    assert driver.step_no == initial_step + 1
    assert "Carol" in driver.last_yield["text"]
    assert "validation_error" in driver.last_yield


def test_progress_hint_tracks_position(hello_run_fn):
    driver = _make_driver(hello_run_fn, names=["A", "B", "C"])
    assert driver.progress_hint == {"current": 1, "of": 3, "label": "greeting 1/3"}
    driver.send({"message": "hi A"})
    assert driver.progress_hint == {"current": 2, "of": 3, "label": "greeting 2/3"}
    driver.send({"message": "hi B"})
    assert driver.progress_hint == {"current": 3, "of": 3, "label": "greeting 3/3"}
