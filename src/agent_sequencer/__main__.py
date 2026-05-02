"""Entry point for the agent-sequencer MCP server.

Launches the MCP server over the stdio transport.
Logs must always be written to stderr (stdout is reserved for JSON-RPC).

CLI:
  --watch    Detect changes in program directories and auto-reload
             (rescans with throttling at the start of
             sequencer_list_programs / sequencer_start / sequencer_resume).

Environment variables:
  AGENT_SEQUENCER_PROGRAMS_DIR  Additional program search path (highest
                                priority). Excluded from the search list
                                when unset.
  AGENT_SEQUENCER_STATE_DIR     Directory where JSONL event logs are stored.
                                Defaults to ~/.claude/sequencer/state/.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

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

    # Prune JSONL files older than the disk TTL once at startup.
    state_dir.mkdir(parents=True, exist_ok=True)
    pruned = prune_old_logs(state_dir)
    if pruned > 0:
        logger.info("Startup prune removed %d JSONL file(s)", pruned)

    mcp = build_server(
        search_paths=search_paths,
        state_dir=state_dir,
        watch=args.watch,
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
      1. $AGENT_SEQUENCER_PROGRAMS_DIR     (bundled programs / dev override)
      2. <cwd>/.claude/sequencer/programs/ (project-specific)
      3. ~/.claude/sequencer/programs/     (user-wide)

    The plugin-bundled programs/ directory is supplied via
    AGENT_SEQUENCER_PROGRAMS_DIR by the plugin's .mcp.json. The MCP server
    does not walk from its own install location to the plugin install
    directory (PyPI install and plugin install live in different places).

    Non-existent paths are skipped by ProgramRegistry.
    """
    paths: list[Path] = []

    env_dir = os.environ.get("AGENT_SEQUENCER_PROGRAMS_DIR")
    if env_dir:
        paths.append(Path(env_dir).expanduser().resolve())

    paths.append((Path.cwd() / ".claude" / "sequencer" / "programs").resolve())
    paths.append((Path.home() / ".claude" / "sequencer" / "programs").resolve())

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


if __name__ == "__main__":
    main()
