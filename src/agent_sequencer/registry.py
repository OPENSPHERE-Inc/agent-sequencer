"""Discovery and loading of sequencer programs.

Dynamically loads `*.py` files via importlib.util and extracts the
NAME / DESCRIPTION / PARAMS_SCHEMA / run metadata.

Discovery policy:
  - `*.py` files directly under each search path (subdirectories are not
    walked).
  - Files starting with `_` are ignored.
  - On name collision, the first match wins (earlier search paths take
    precedence).

Step 7 added hot reload (`rescan`) and prioritization across multiple
search paths. A source_hash change caused by reloading during hot reload
is detected at resume time as a `ProgramChanged` error (implementation
plan §8.2).
"""

from __future__ import annotations

import hashlib
import logging
import types
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ProgramEntry:
    """A single loaded sequencer program."""

    name: str
    description: str
    params_schema: dict[str, Any]
    run_fn: Callable[..., Any]
    source_path: Path
    source_hash: str  # used for resume-time integrity check and rescan diffing


class ProgramRegistry:
    """Reads `*.py` files under the search paths and builds a name -> ProgramEntry map."""

    def __init__(self, search_paths: list[Path]):
        self._search_paths: list[Path] = list(search_paths)
        self._programs: dict[str, ProgramEntry] = {}
        self._scan_all(self._programs)
        logger.info(
            "Loaded %d program(s): %s",
            len(self._programs),
            ", ".join(self._programs.keys()) or "(none)",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get(self, name: str) -> ProgramEntry | None:
        """Look up a program by name. Returns None if absent."""
        return self._programs.get(name)

    def list_all(self) -> list[ProgramEntry]:
        """Return all programs."""
        return list(self._programs.values())

    def rescan(self) -> dict[str, list[str]]:
        """Rescan the search paths and reload any programs that have changed.

        Differences are detected by comparing source_hash. Active instances
        keep using the `run_fn` they captured (held inside the Driver), so
        they are unaffected by reload (subsequent sequencer_start calls
        will use the new function).

        Returns:
            Diff summary {"added": [...], "updated": [...], "removed": [...]}.
            All-empty lists mean nothing changed.
        """
        new_programs: dict[str, ProgramEntry] = {}
        self._scan_all(new_programs)

        old_names = set(self._programs.keys())
        new_names = set(new_programs.keys())

        added = sorted(new_names - old_names)
        removed = sorted(old_names - new_names)
        updated = sorted(
            name
            for name in old_names & new_names
            if self._programs[name].source_hash != new_programs[name].source_hash
        )

        self._programs = new_programs

        if added or updated or removed:
            logger.info(
                "Reloaded programs: added=%s updated=%s removed=%s",
                added,
                updated,
                removed,
            )

        return {"added": added, "updated": updated, "removed": removed}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _scan_all(self, target: dict[str, ProgramEntry]) -> None:
        """Walk every search path and populate `target` (first match wins)."""
        for root in self._search_paths:
            self._scan(root, target)

    def _scan(self, root: Path, target: dict[str, ProgramEntry]) -> None:
        """Walk a single search path and register programs into `target`."""
        if not root.is_dir():
            logger.debug("Search path does not exist: %s", root)
            return
        for py_file in sorted(root.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            self._load(py_file, target)

    def _load(self, py_file: Path, target: dict[str, ProgramEntry]) -> None:
        """Load one file and add a ProgramEntry to `target`.

        Load failures and missing required attributes log a warning and are
        skipped (non-fatal).

        Instead of going through importlib.util.spec_from_file_location, we
        read the source each time and compile + exec it. The reasons:
          - When the same file path is read repeatedly during hot reload
            (rescan), SourceFileLoader uses a pyc cache keyed on
            mtime + size. If a content change preserves size and shares
            the same second-precision mtime, stale code can persist.
          - Here we never produce pyc files, so we don't depend on the
            precision of mtime/size.
        """
        try:
            source_bytes = py_file.read_bytes()
        except OSError as e:
            logger.warning("Cannot read source file: %s (%s)", py_file, e)
            return

        source_hash = hashlib.sha256(source_bytes).hexdigest()
        # Build a unique module name from the file path plus the source hash
        # to avoid same-name collisions.
        mod_name = (
            f"_sequencer_program_{py_file.parent.name}_{py_file.stem}_{source_hash[:12]}"
        )
        module = types.ModuleType(mod_name)
        module.__file__ = str(py_file)

        try:
            code = compile(source_bytes, str(py_file), "exec")
            exec(code, module.__dict__)
        except Exception as e:
            logger.warning("Failed to load program: %s (%s)", py_file, e)
            return

        run_fn = getattr(module, "run", None)
        if not callable(run_fn):
            logger.warning(
                "%s: skipping because run() function is not defined", py_file
            )
            return

        name = getattr(module, "NAME", py_file.stem)
        if name in target:
            logger.debug(
                "Duplicate program name (first wins): %s (skipping %s)",
                name,
                py_file,
            )
            return

        target[name] = ProgramEntry(
            name=name,
            description=getattr(module, "DESCRIPTION", ""),
            params_schema=getattr(module, "PARAMS_SCHEMA", {}),
            run_fn=run_fn,
            source_path=py_file,
            source_hash=source_hash,
        )
        logger.debug("Loaded program: %s (%s)", name, py_file)
