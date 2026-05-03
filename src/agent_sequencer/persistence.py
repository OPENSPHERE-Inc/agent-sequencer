"""Persistence to and replay from JSONL event logs.

Each instance's events are appended to
~/.claude/sequencer/state/<instance_id>.jsonl (one event per line,
append-only).

Event types:
  - header: Written once at instance start.
            Contains instance_id / program / source_hash / params /
            started_at.
  - yield:  Written each time the Driver yields.
            step_no / yielded_at / payload (= last_yield).
  - result: Written for every result that sequencer_next passes to
            Driver.send. for_step_no / result / received_at.

On resume, the log is read, header.source_hash is checked against the
current source, then a fresh Driver is built and the result events are
re-injected into gen.send in order (deterministic replay /
Temporal/Restate-style). If the program is deterministic, the final state
matches the original instance.

The generator itself is never pickled. Programs must not directly use
time, randomness, or external I/O (this is documented in the authoring
guide).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# String constants for event types.
EVENT_HEADER = "header"
EVENT_YIELD = "yield"
EVENT_RESULT = "result"

# Terminal kinds matching last_yield.kind (kept consistent with runtime.py).
_KIND_DONE = "done"
_KIND_ABORT = "abort"
_KIND_ERROR = "error"

# Default disk TTLs (implementation plan § 7).
DISK_TTL_COMPLETED = timedelta(days=30)
DISK_TTL_ABORTED = timedelta(days=7)
DISK_TTL_FAILED = timedelta(days=90)
DISK_TTL_INTERRUPTED = timedelta(days=7)  # server died before reaching a terminal state


# ----------------------------------------------------------------------
# Writer side: append-only JSONL handle.
# ----------------------------------------------------------------------
class EventLog:
    """Append-only JSONL write handle for a single instance.

    Each append flushes immediately, so even an abrupt server crash keeps
    every event up to the last one persisted on disk.
    """

    def __init__(self, path: Path):
        # Initialize before any I/O so __del__ does not raise AttributeError.
        self._fp = None
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = self.path.open("a", encoding="utf-8")

    def append(self, event: dict[str, Any]) -> None:
        """Write a single event and flush immediately."""
        self._fp.write(json.dumps(event, ensure_ascii=False))
        self._fp.write("\n")
        self._fp.flush()

    def close(self) -> None:
        """Close the file handle."""
        if self._fp is None:
            return
        if not self._fp.closed:
            self._fp.close()

    def __del__(self) -> None:
        # Also close on GC as a safety net for callers who forgot to call close().
        try:
            self.close()
        except Exception:
            pass


# ----------------------------------------------------------------------
# Event construction helpers
# ----------------------------------------------------------------------
def header_event(
    instance_id: str,
    program: str,
    source_hash: str,
    params: dict[str, Any],
    started_at: datetime,
) -> dict[str, Any]:
    """Build the header event written once at instance start."""
    return {
        "type": EVENT_HEADER,
        "instance_id": instance_id,
        "program": program,
        "source_hash": source_hash,
        "params": params,
        "started_at": started_at.isoformat(),
    }


def yield_event(
    step_no: int,
    payload: dict[str, Any] | None,
    yielded_at: datetime | None,
) -> dict[str, Any]:
    """Build the yield event written each time the Driver yields."""
    return {
        "type": EVENT_YIELD,
        "step_no": step_no,
        "yielded_at": yielded_at.isoformat() if yielded_at is not None else None,
        "payload": payload,
    }


def result_event(for_step_no: int, result: Any) -> dict[str, Any]:
    """Build the result event recording what sequencer_next handed to the Driver."""
    return {
        "type": EVENT_RESULT,
        "for_step_no": for_step_no,
        "result": result,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }


# ----------------------------------------------------------------------
# Reader side
# ----------------------------------------------------------------------
class CorruptedLogError(ValueError):
    """The log file is unexpected (missing header, unparseable JSON, empty file)."""


def read_events(path: Path) -> list[dict[str, Any]]:
    """Read all events and return them as a list.

    Trailing newline-less lines and JSON parse errors are skipped with a
    warning log (this rescues cases where the tail is truncated due to a
    crash).
    """
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning(
                    "%s line %d: skipping due to JSON parse failure (%s)",
                    path,
                    lineno,
                    e,
                )
    return events


def parse_header(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify that the first event is a header and return it.

    Raises:
        CorruptedLogError: When the events list is empty or the first event
            is not a header.
    """
    if not events:
        raise CorruptedLogError("Log contains no events")
    first = events[0]
    if first.get("type") != EVENT_HEADER:
        raise CorruptedLogError(
            f"First event is not a header: type='{first.get('type')}'"
        )
    return first


def iter_results(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only the result events in order (used during replay)."""
    return [ev for ev in events if ev.get("type") == EVENT_RESULT]


# ----------------------------------------------------------------------
# Disk TTL (ARCHIVED -> PRUNED)
# ----------------------------------------------------------------------
def _last_yield_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the last yield event in the list (None if there is none)."""
    for ev in reversed(events):
        if ev.get("type") == EVENT_YIELD:
            return ev
    return None


def determine_disk_ttl(events: list[dict[str, Any]]) -> timedelta:
    """Determine the disk retention period for an instance from its events.

    Inspect the kind of the trailing yield event and return one of:
    completed 30 days / aborted or interrupted 7 days / failed 90 days /
    no terminal yield 7 days.
    """
    last_yield = _last_yield_event(events)
    last_yield_kind: str | None = None
    if last_yield is not None:
        last_yield_kind = (last_yield.get("payload") or {}).get("kind")

    if last_yield_kind == _KIND_DONE:
        return DISK_TTL_COMPLETED
    if last_yield_kind == _KIND_ABORT:
        return DISK_TTL_ABORTED
    if last_yield_kind == _KIND_ERROR:
        return DISK_TTL_FAILED
    # kind=instruction (no terminal reached) or no yield events at all.
    return DISK_TTL_INTERRUPTED


def _last_yield_timestamp(events: list[dict[str, Any]]) -> datetime | None:
    """Return the trailing yield event's yielded_at as an aware datetime.

    Returns None on lookup or parse failure. Callers should fall back to
    file mtime.
    """
    last_yield = _last_yield_event(events)
    if last_yield is None:
        return None
    raw = last_yield.get("yielded_at")
    if not isinstance(raw, str):
        return None
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError as e:
        logger.warning("Failed to parse yielded_at as ISO 8601: %r (%s)", raw, e)
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def prune_old_logs(state_dir: Path) -> int:
    """Delete `*.jsonl` files under state_dir whose age exceeds the TTL.

    Designed to be called once at startup (no background processing).
    Returns the number of files deleted. Age is measured from the trailing
    yield's yielded_at, falling back to file mtime when that is unavailable.
    """
    if not state_dir.is_dir():
        return 0

    now = datetime.now(timezone.utc)
    deleted = 0

    for log_path in sorted(state_dir.glob("*.jsonl")):
        try:
            events = read_events(log_path)
            ttl = determine_disk_ttl(events)

            reference = _last_yield_timestamp(events)
            if reference is None:
                mtime_ts = log_path.stat().st_mtime
                reference = datetime.fromtimestamp(mtime_ts, tz=timezone.utc)
            age = now - reference

            if age > ttl:
                log_path.unlink()
                logger.info(
                    "Deleted JSONL exceeding TTL: %s (age=%s, ttl=%s)",
                    log_path.name,
                    age,
                    ttl,
                )
                deleted += 1
        except Exception as e:
            logger.warning(
                "Error while pruning log: %s (%s)", log_path, e
            )

    return deleted
