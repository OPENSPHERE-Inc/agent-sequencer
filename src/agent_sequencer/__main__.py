"""Entry point for the agent-sequencer MCP server.

Launches the MCP server over the stdio transport.
Logs must always be written to stderr (stdout is reserved for JSON-RPC).

CLI:
  --watch    Detect changes in program directories and auto-reload
             (rescans with throttling at the start of
             sequencer_list_programs / sequencer_start / sequencer_resume).

Environment variables:
  AGENT_SEQUENCER_PROGRAMS_DIR        Additional program search path
                                      appended as the lowest-priority
                                      fallback (used by plugins to ship
                                      bundled programs that user /
                                      project paths can override).
                                      Excluded from the search list when
                                      unset.
  AGENT_SEQUENCER_STATE_DIR           Directory where JSONL event logs
                                      are stored. Defaults to
                                      ~/.claude/sequencer/state/.
  AGENT_SEQUENCER_MEMO_VALUE_LIMIT    Per-value byte ceiling for the
                                      sequencer_memo_* tools. Defaults
                                      to 1 MiB.
  AGENT_SEQUENCER_MEMO_INSTANCE_LIMIT Per-instance total byte ceiling
                                      for the sequencer_memo_* tools.
                                      Defaults to 64 MiB.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .memo import (
    DEFAULT_INSTANCE_LIMIT as MEMO_DEFAULT_INSTANCE_LIMIT,
    DEFAULT_VALUE_LIMIT as MEMO_DEFAULT_VALUE_LIMIT,
)
from .persistence import prune_old_logs
from .tools import build_server


def main() -> None:
    """Launch the MCP server over stdio."""
    args = _parse_args()

    # Logs must go to stderr; stdout is reserved for JSON-RPC traffic.
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )

    logger = logging.getLogger(__name__)
    logger.info(
        "Starting agent-sequencer MCP server (stdio, watch=%s)", args.watch
    )

    search_paths = _resolve_search_paths()
    logger.info(
        "Program search paths (in priority order): %s",
        ", ".join(str(p) for p in search_paths) or "(none)",
    )

    state_dir = _resolve_state_dir()
    logger.info("State directory: %s", state_dir)

    memo_value_limit, memo_instance_limit = _resolve_memo_limits(logger)
    logger.info(
        "Memo quotas: per-value=%d bytes, per-instance=%d bytes",
        memo_value_limit,
        memo_instance_limit,
    )

    # Prune JSONL files older than the disk TTL once at startup.
    state_dir.mkdir(parents=True, exist_ok=True)
    pruned = prune_old_logs(state_dir)
    if pruned > 0:
        logger.info("Startup prune removed %d JSONL file(s)", pruned)

    mcp = build_server(
        search_paths=search_paths,
        state_dir=state_dir,
        watch=args.watch,
        memo_value_limit=memo_value_limit,
        memo_instance_limit=memo_instance_limit,
    )
    mcp.run(transport="stdio")


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="agent-sequencer",
        description=(
            "MCP server that step-drives AI agents using classical programs "
            "(sequencer programs)"
        ),
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help=(
            "Detect changes in program directories and auto-reload "
            "(for development)"
        ),
    )
    return parser.parse_args()


def _resolve_search_paths() -> list[Path]:
    """Build the program search path list (first-match-wins order).

    Priority:
      1. <cwd>/.claude/sequencer/programs/ (project-specific)
      2. ~/.claude/sequencer/programs/     (user-wide)
      3. $AGENT_SEQUENCER_PROGRAMS_DIR     (bundled programs / dev override)

    The plugin-bundled programs/ directory is supplied via
    AGENT_SEQUENCER_PROGRAMS_DIR by the plugin's .mcp.json. It is appended
    last so a user-wide or project-specific program with the same NAME
    transparently overrides the plugin-bundled copy. The MCP server does
    not walk from its own install location to the plugin install directory
    (PyPI install and plugin install live in different places).

    Non-existent paths are skipped by ProgramRegistry.
    """
    paths: list[Path] = []

    paths.append((Path.cwd() / ".claude" / "sequencer" / "programs").resolve())
    paths.append((Path.home() / ".claude" / "sequencer" / "programs").resolve())

    env_dir = os.environ.get("AGENT_SEQUENCER_PROGRAMS_DIR")
    if env_dir:
        paths.append(Path(env_dir).expanduser().resolve())

    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def _resolve_state_dir() -> Path:
    """Resolve the JSONL event log directory from the environment.

    Defaults to ~/.claude/sequencer/state/, created on first use.
    """
    env_dir = os.environ.get("AGENT_SEQUENCER_STATE_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return Path.home() / ".claude" / "sequencer" / "state"


def _resolve_memo_limits(
    logger: logging.Logger,
) -> tuple[int, int]:
    """Resolve memo quotas from AGENT_SEQUENCER_MEMO_* env vars.

    Falls back to the defaults exported from agent_sequencer.memo when
    the variables are unset or contain a non-positive integer; in the
    latter case a warning is logged so misconfigurations are visible.
    """
    return (
        _read_positive_int_env(
            "AGENT_SEQUENCER_MEMO_VALUE_LIMIT",
            MEMO_DEFAULT_VALUE_LIMIT,
            logger,
        ),
        _read_positive_int_env(
            "AGENT_SEQUENCER_MEMO_INSTANCE_LIMIT",
            MEMO_DEFAULT_INSTANCE_LIMIT,
            logger,
        ),
    )


def _read_positive_int_env(
    name: str, default: int, logger: logging.Logger
) -> int:
    """Read a positive integer from an env var, falling back on errors."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid %s=%r (not an integer), using default %d",
            name,
            raw,
            default,
        )
        return default
    if value <= 0:
        logger.warning(
            "Invalid %s=%d (must be positive), using default %d",
            name,
            value,
            default,
        )
        return default
    return value


if __name__ == "__main__":
    main()
