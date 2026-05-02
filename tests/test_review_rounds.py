"""Verify the convergence logic of the bundled review-rounds program via Driver."""

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
def review_rounds_run_fn(bundled_programs_dir):
    if not bundled_programs_dir.exists():
        pytest.skip("bundled programs directory not present")
    reg = ProgramRegistry([bundled_programs_dir])
    entry = reg.get("review-rounds")
    if entry is None:
        pytest.skip("review-rounds program not loaded")
    return entry.run_fn


def _make_driver(run_fn, **params):
    driver = Driver(run_fn, params=params)
    driver.start()
    return driver


def test_zero_findings_converges_immediately(review_rounds_run_fn):
    driver = _make_driver(review_rounds_run_fn, max_rounds=3, base="main")
    assert driver.last_yield["kind"] == KIND_INSTRUCTION
    driver.send({"doc_path": "review.md", "findings_total": 0})
    assert driver.last_yield["kind"] == KIND_DONE
    summary = driver.last_yield["summary"]
    assert summary["converged"] is True
    assert summary["rounds_executed"] == 1
    assert "zero" in summary["reason"].lower()


def test_no_code_change_converges(review_rounds_run_fn):
    driver = _make_driver(review_rounds_run_fn, max_rounds=3, base="main")
    driver.send({"doc_path": "review.md", "findings_total": 5})  # Step 1
    driver.send({"fixed_count": 0, "wontfix_count": 5, "code_changed": False})  # Step 2
    driver.send({"unresolved_count": 5})  # Step 3
    assert driver.last_yield["kind"] == KIND_DONE
    summary = driver.last_yield["summary"]
    assert summary["converged"] is True
    assert summary["total_wontfix"] == 5


def test_max_rounds_aborts(review_rounds_run_fn):
    driver = _make_driver(review_rounds_run_fn, max_rounds=2, base="main")

    # Round 1: findings produced -> fix -> verify
    driver.send({"doc_path": "review.md", "findings_total": 3})
    driver.send({"fixed_count": 2, "wontfix_count": 0, "code_changed": True})
    driver.send({"unresolved_count": 1})

    # Round 2: findings remain -> fix -> verify
    driver.send({"doc_path": "review.md", "findings_total": 1})
    driver.send({"fixed_count": 1, "wontfix_count": 0, "code_changed": True})
    driver.send({"unresolved_count": 0})

    assert driver.last_yield["kind"] == KIND_ABORT
    assert "2" in driver.last_yield["reason"]


def test_target_specified_includes_path_in_instruction(review_rounds_run_fn):
    driver = _make_driver(
        review_rounds_run_fn,
        max_rounds=1,
        base="main",
        target="src/agent_sequencer",
    )
    text = driver.last_yield["text"]
    assert "src/agent_sequencer" in text


def test_target_omitted_uses_repo_diff(review_rounds_run_fn):
    driver = _make_driver(review_rounds_run_fn, max_rounds=1, base="main")
    text = driver.last_yield["text"]
    assert "entire diff" in text or "entire repository" in text
