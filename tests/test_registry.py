"""Unit tests for ProgramRegistry discovery and loading."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from agent_sequencer.registry import ProgramRegistry


def _write_program(dir_: Path, name: str, body: str) -> Path:
    path = dir_ / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    return path


_HELLO_BODY = dedent('''
    from agent_sequencer.api import Done, Instruction
    NAME = "hello"
    DESCRIPTION = "Greeting program."
    PARAMS_SCHEMA = {"name": {"type": "string"}}

    def run(ctx):
        result = yield Instruction(text="hi", expect_schema={"type": "object"})
        yield Done(summary={"echo": result})
    ''').strip()


def test_loads_program_from_directory(tmp_path):
    _write_program(tmp_path, "hello", _HELLO_BODY)
    reg = ProgramRegistry([tmp_path])
    entry = reg.get("hello")
    assert entry is not None
    assert entry.name == "hello"
    assert entry.description == "Greeting program."
    assert entry.source_hash  # not empty


def test_first_path_wins_on_name_collision(tmp_path):
    p1 = tmp_path / "primary"
    p2 = tmp_path / "secondary"
    p1.mkdir()
    p2.mkdir()
    _write_program(p1, "hello", _HELLO_BODY.replace("Greeting program.", "Primary"))
    _write_program(p2, "hello", _HELLO_BODY.replace("Greeting program.", "Secondary"))
    reg = ProgramRegistry([p1, p2])
    assert reg.get("hello").description == "Primary"


def test_underscore_files_are_ignored(tmp_path):
    _write_program(tmp_path, "_helper", _HELLO_BODY)
    reg = ProgramRegistry([tmp_path])
    assert reg.list_all() == []


def test_rescan_detects_added_updated_removed(tmp_path):
    _write_program(tmp_path, "hello", _HELLO_BODY)
    reg = ProgramRegistry([tmp_path])

    _write_program(
        tmp_path,
        "hello",
        _HELLO_BODY.replace("Greeting program.", "Updated"),
    )
    _write_program(tmp_path, "world", _HELLO_BODY.replace("hello", "world"))

    diff = reg.rescan()
    assert "world" in diff["added"]
    assert "hello" in diff["updated"]


def test_bundled_review_rounds_loads(bundled_programs_dir):
    """The bundled review-rounds program loads successfully."""
    if not bundled_programs_dir.exists():
        pytest.skip("bundled programs directory not present in this checkout")
    reg = ProgramRegistry([bundled_programs_dir])
    entry = reg.get("review-rounds")
    assert entry is not None
    assert entry.params_schema  # has parameters
    assert callable(entry.run_fn)
