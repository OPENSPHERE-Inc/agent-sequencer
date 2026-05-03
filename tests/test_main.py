"""Unit tests for the CLI entry point's search-path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_sequencer.__main__ import _resolve_search_paths


def _patch_home_and_cwd(monkeypatch: pytest.MonkeyPatch, home: Path, cwd: Path) -> None:
    """Redirect Path.home() / Path.cwd() to the supplied tmp_path subdirectories."""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(cwd)


def test_env_dir_is_appended_last(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`$AGENT_SEQUENCER_PROGRAMS_DIR` must come last so user/project paths win."""
    env_dir = tmp_path / "env_programs"
    env_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    monkeypatch.setenv("AGENT_SEQUENCER_PROGRAMS_DIR", str(env_dir))
    _patch_home_and_cwd(monkeypatch, home, cwd)

    paths = _resolve_search_paths()

    expected_cwd = (cwd / ".claude" / "sequencer" / "programs").resolve()
    expected_home = (home / ".claude" / "sequencer" / "programs").resolve()
    expected_env = env_dir.resolve()

    assert paths == [expected_cwd, expected_home, expected_env]


def test_env_dir_omitted_when_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When unset, only cwd and home paths are returned, in that order."""
    home = tmp_path / "home"
    home.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    monkeypatch.delenv("AGENT_SEQUENCER_PROGRAMS_DIR", raising=False)
    _patch_home_and_cwd(monkeypatch, home, cwd)

    paths = _resolve_search_paths()

    expected_cwd = (cwd / ".claude" / "sequencer" / "programs").resolve()
    expected_home = (home / ".claude" / "sequencer" / "programs").resolve()

    assert paths == [expected_cwd, expected_home]


def test_duplicate_paths_are_deduplicated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If env_dir resolves to the same path as cwd/home, it appears only once."""
    home = tmp_path / "home"
    home.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    cwd_programs = cwd / ".claude" / "sequencer" / "programs"
    cwd_programs.mkdir(parents=True)

    monkeypatch.setenv("AGENT_SEQUENCER_PROGRAMS_DIR", str(cwd_programs))
    _patch_home_and_cwd(monkeypatch, home, cwd)

    paths = _resolve_search_paths()

    expected_cwd = cwd_programs.resolve()
    expected_home = (home / ".claude" / "sequencer" / "programs").resolve()

    # The env entry is the duplicate; the dedupe pass keeps the first occurrence.
    assert paths == [expected_cwd, expected_home]
