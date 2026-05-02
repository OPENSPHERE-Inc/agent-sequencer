"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_PROGRAMS_DIR = REPO_ROOT / "skills" / "agent-sequencer" / "programs"


@pytest.fixture(scope="session")
def bundled_programs_dir() -> Path:
    """Absolute path to the bundled programs/ directory."""
    return BUNDLED_PROGRAMS_DIR


@pytest.fixture
def state_dir(tmp_path: pytest.TempPathFactory) -> Path:
    """JSONL state directory for tests."""
    d = tmp_path / "state"
    d.mkdir()
    return d
