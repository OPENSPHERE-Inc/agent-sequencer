"""Definition and registration of MCP tools.

Implemented (steps 2-6):
  - sequencer_list_programs:  Listing from the registry.
  - sequencer_start:          Start the Driver, create an Instance, and
                              write the header and initial yield events to
                              JSONL.
  - sequencer_current:        Return the latest last_yield (read-only, no
                              lock needed). Returns NotLoaded if archived,
                              NotFound if there is no log.
  - sequencer_next:           Check for_step_no consistency -> write the
                              result event -> inject the result into the
                              Driver -> write the new yield event.
                              Concurrent calls against the same instance
                              are serialized by instance.lock.
  - sequencer_resume:         Restore from JSONL with source_hash check.
  - sequencer_list:           List instances currently held in memory.
  - sequencer_close:          Archive an instance (release memory).
                              Idempotent. delete_log=True also removes the
                              JSONL.

Lifecycle:
  - Instances older than TERMINAL_TTL (default 10 minutes) past their
    terminal time are auto-removed from memory by the sweep (ARCHIVED
    state). Re-access them via sequencer_resume.
  - Disk TTL is handled by the startup prune in __main__.py.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .instance import Instance, InstanceStore
from .memo import (
    DEFAULT_INSTANCE_LIMIT as MEMO_DEFAULT_INSTANCE_LIMIT,
    DEFAULT_VALUE_LIMIT as MEMO_DEFAULT_VALUE_LIMIT,
    InvalidMemoKey,
    MemoQuotaExceeded,
    MemoStore,
)
from .persistence import (
    CorruptedLogError,
    EVENT_HEADER,
    EVENT_RESULT,
    EventLog,
    header_event,
    parse_header,
    read_events,
    result_event,
    yield_event,
)
from .registry import ProgramRegistry
from .runtime import Driver

logger = logging.getLogger(__name__)

SERVER_NAME = "agent-sequencer"

# Maximum time to keep an instance in memory after reaching a terminal
# state (implementation plan § 7; median 5-10 minutes).
TERMINAL_TTL = timedelta(minutes=10)

# Minimum interval between sweeps. We run on every tool call but throttle
# the actual work.
_SWEEP_THROTTLE_SECONDS = 60.0

# Minimum interval between registry rescans under --watch.
# Avoids excessive rescans while still picking up edits before the next
# tool call.
_RESCAN_THROTTLE_SECONDS = 2.0


def build_server(
    search_paths: list[Path],
    state_dir: Path,
    watch: bool = False,
    memo_value_limit: int = MEMO_DEFAULT_VALUE_LIMIT,
    memo_instance_limit: int = MEMO_DEFAULT_INSTANCE_LIMIT,
) -> FastMCP:
    """Build the FastMCP server instance, register all tools, and return it.

    Args:
        search_paths:        List of program search paths.
        state_dir:           Directory where JSONL event logs are stored.
        watch:               When True, detect changes under the program
                             search paths and auto-reload at the start of
                             sequencer_list_programs / sequencer_start /
                             sequencer_resume (throttled). Active
                             instances are unaffected (the Driver
                             continues using the run_fn it already
                             captured).
        memo_value_limit:    Maximum encoded UTF-8 byte size of a single
                             memo value.
        memo_instance_limit: Maximum total encoded byte size across all
                             memo entries for one instance.
    """
    mcp = FastMCP(SERVER_NAME)

    # ----------------------------------------------------------------
    # Server-internal state
    # ----------------------------------------------------------------
    state_dir.mkdir(parents=True, exist_ok=True)
    logger.info("State directory: %s", state_dir)

    registry = ProgramRegistry(search_paths)
    store = InstanceStore()
    memo = MemoStore(
        value_limit=memo_value_limit,
        instance_limit=memo_instance_limit,
    )

    # Throttle state for the terminal-TTL sweep (monotonic time of the
    # last sweep).
    sweep_state = {"last": 0.0}
    # Throttle state for the --watch rescan.
    rescan_state = {"last": 0.0}

    def maybe_rescan_programs() -> None:
        """Rescan the registry under --watch (throttled).

        Does nothing when watch=False. The registry layer detects diffs by
        source_hash, so unchanged scans are a no-op (the I/O cost is small).
        """
        if not watch:
            return
        now_mono = time.monotonic()
        if now_mono - rescan_state["last"] < _RESCAN_THROTTLE_SECONDS:
            return
        rescan_state["last"] = now_mono
        registry.rescan()

    def maybe_sweep_terminal() -> None:
        """Evict instances from memory that have exceeded the terminal TTL.

        Called at the top of every tool. The actual work is skipped when
        less than _SWEEP_THROTTLE_SECONDS has passed since the previous
        sweep.
        """
        now_mono = time.monotonic()
        if now_mono - sweep_state["last"] < _SWEEP_THROTTLE_SECONDS:
            return
        sweep_state["last"] = now_mono

        now = datetime.now(timezone.utc)
        targets: list[Instance] = []
        for inst in store.list_all():
            if inst.driver.terminal_at is None:
                continue
            elapsed = now - inst.driver.terminal_at
            if elapsed > TERMINAL_TTL:
                targets.append(inst)

        for inst in targets:
            removed = store.remove(inst.instance_id)
            if removed is None:
                continue
            if removed.event_log is not None:
                removed.event_log.close()
            memo.clear_instance(inst.instance_id)
            logger.info(
                "Archived instance after TTL elapsed: id=%s state=%s",
                inst.instance_id,
                inst.driver.state,
            )

    # ----------------------------------------------------------------
    # sequencer_list_programs
    # ----------------------------------------------------------------
    @mcp.tool()
    async def sequencer_list_programs(filter: str | None = None) -> dict[str, Any]:
        """Return the list of available sequencer programs."""
        maybe_sweep_terminal()
        maybe_rescan_programs()
        entries = registry.list_all()
        if filter:
            entries = [e for e in entries if filter in e.name]
        return {
            "programs": [
                {
                    "name": e.name,
                    "description": e.description,
                    "params_schema": e.params_schema,
                }
                for e in entries
            ],
        }

    # ----------------------------------------------------------------
    # sequencer_start
    # ----------------------------------------------------------------
    @mcp.tool()
    async def sequencer_start(
        program: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a new instance of a sequencer program."""
        maybe_sweep_terminal()
        maybe_rescan_programs()
        entry = registry.get(program)
        if entry is None:
            return _error(
                "ProgramNotFound",
                f"Program '{program}' not found. "
                "Use sequencer_list_programs to see what is available.",
            )

        actual_params = params or {}
        try:
            driver = Driver(entry.run_fn, params=actual_params)
            driver.start()
        except Exception as e:
            logger.exception("sequencer_start failed to launch the driver")
            return _error(
                "StartFailed",
                f"Failed to start program: {type(e).__name__}: {e}",
            )

        instance_id = store.new_instance_id()
        event_log = EventLog(state_dir / f"{instance_id}.jsonl")
        instance = Instance(
            instance_id=instance_id,
            program_name=entry.name,
            params=actual_params,
            driver=driver,
            event_log=event_log,
        )
        store.add(instance)

        event_log.append(
            header_event(
                instance_id=instance_id,
                program=entry.name,
                source_hash=entry.source_hash,
                params=actual_params,
                started_at=instance.started_at,
            )
        )
        event_log.append(
            yield_event(
                step_no=instance.step_no,
                payload=instance.last_yield,
                yielded_at=instance.driver.yielded_at,
            )
        )

        return _instance_response(instance)

    # ----------------------------------------------------------------
    # sequencer_current
    # ----------------------------------------------------------------
    @mcp.tool()
    async def sequencer_current(instance_id: str) -> dict[str, Any]:
        """Re-fetch the most recent yield value (used to resync).

        Do not use this return value to decide what to do next; always
        follow the instruction in last_yield itself.

        Returns NotLoaded (with a resume hint) when the instance is not in
        memory but a JSONL exists, and NotFound when no log exists either.
        """
        maybe_sweep_terminal()
        instance = store.get(instance_id)
        if instance is None:
            return _not_in_memory_response(state_dir, instance_id)
        return _instance_response(instance)

    # ----------------------------------------------------------------
    # sequencer_next
    # ----------------------------------------------------------------
    @mcp.tool()
    async def sequencer_next(
        instance_id: str,
        for_step_no: int,
        result: Any,
    ) -> dict[str, Any]:
        """Submit the result for the previous instruction and fetch the next last_yield.

        Returns NotLoaded / NotFound when the instance is not in memory
        (resume is required).
        """
        maybe_sweep_terminal()
        instance = store.get(instance_id)
        if instance is None:
            return _not_in_memory_response(state_dir, instance_id)

        async with instance.lock:
            # Re-evaluate: the instance may have been archived by sweep or
            # close while we were waiting on the lock.
            if store.get(instance_id) is None:
                return _not_in_memory_response(state_dir, instance_id)

            if instance.driver.is_terminal():
                return _error(
                    "TerminalError",
                    f"Instance is already in a terminal state (state={instance.state}). "
                    "Start a new instance with sequencer_start.",
                )

            if for_step_no != instance.step_no:
                return _error(
                    "OutOfSync",
                    f"Awaiting step_no {instance.step_no} but received "
                    f"for_step_no={for_step_no}. "
                    "Call sequencer_current to fetch the current state and resubmit.",
                )

            # Clear the memo before advancing the Driver so that any
            # entries written during the previous Instruction's
            # execution do not leak into the next step. This is what
            # makes cross-step memo retention structurally impossible
            # and keeps resume's empty-memo state equivalent to a
            # never-archived run's state at the same step boundary.
            memo.clear_instance(instance_id)

            if instance.event_log is not None:
                instance.event_log.append(
                    result_event(for_step_no=for_step_no, result=result)
                )

            try:
                instance.driver.send(result)
            except Exception as e:
                logger.exception("sequencer_next: send failed")
                if instance.event_log is not None:
                    instance.event_log.append(
                        yield_event(
                            step_no=instance.step_no,
                            payload=instance.last_yield,
                            yielded_at=instance.driver.yielded_at,
                        )
                    )
                return _error(
                    "AdvanceFailed",
                    f"Failed to advance the program: {type(e).__name__}: {e}",
                )

            if instance.event_log is not None:
                instance.event_log.append(
                    yield_event(
                        step_no=instance.step_no,
                        payload=instance.last_yield,
                        yielded_at=instance.driver.yielded_at,
                    )
                )

            return _instance_response(instance)

    # ----------------------------------------------------------------
    # sequencer_resume
    # ----------------------------------------------------------------
    @mcp.tool()
    async def sequencer_resume(instance_id: str) -> dict[str, Any]:
        """Restore an instance from its JSONL event log."""
        maybe_sweep_terminal()
        maybe_rescan_programs()
        if store.get(instance_id) is not None:
            return _error(
                "AlreadyLoaded",
                f"Instance '{instance_id}' is already loaded in memory.",
            )

        log_path = state_dir / f"{instance_id}.jsonl"
        if not log_path.exists():
            return _error(
                "NotFound",
                f"Event log does not exist: {log_path.name}",
            )

        try:
            events = read_events(log_path)
            header = parse_header(events)
        except CorruptedLogError as e:
            return _error("CorruptedLog", f"Log is corrupted: {e}")
        except Exception as e:
            logger.exception("Failed to read log")
            return _error("CorruptedLog", f"Failed to read log: {e}")

        program_name = header["program"]
        entry = registry.get(program_name)
        if entry is None:
            return _error(
                "ProgramNotFound",
                f"Program '{program_name}' is not in the current registry. "
                "It may have been deleted or removed from the search paths.",
            )

        if entry.source_hash != header["source_hash"]:
            return _error(
                "ProgramChanged",
                f"Program '{program_name}' source has changed. "
                "Refusing to restore because deterministic replay may break. "
                f"recorded hash={header['source_hash'][:12]} / "
                f"current hash={entry.source_hash[:12]}",
            )

        try:
            driver = Driver(entry.run_fn, params=header["params"])
            driver.start()
            for ev in events[1:]:
                if ev.get("type") != EVENT_RESULT:
                    continue
                if driver.is_terminal():
                    logger.warning(
                        "During replay: Driver is terminal but result events remain"
                        " (instance_id=%s)",
                        instance_id,
                    )
                    break
                driver.send(ev["result"])
        except Exception as e:
            logger.exception("Exception during replay")
            return _error(
                "ReplayFailed",
                f"Replay failed: {type(e).__name__}: {e}",
            )

        try:
            started_at = datetime.fromisoformat(header["started_at"])
        except Exception:
            started_at = datetime.now(timezone.utc)

        instance = Instance(
            instance_id=instance_id,
            program_name=program_name,
            params=header["params"],
            driver=driver,
            started_at=started_at,
            event_log=None if driver.is_terminal() else EventLog(log_path),
        )
        store.add(instance)
        logger.info(
            "Replayed instance: id=%s state=%s step_no=%d",
            instance_id,
            instance.state,
            instance.step_no,
        )
        return _instance_response(instance)

    # ----------------------------------------------------------------
    # sequencer_close
    # ----------------------------------------------------------------
    @mcp.tool()
    async def sequencer_close(
        instance_id: str,
        delete_log: bool = False,
    ) -> dict[str, Any]:
        """Close an instance and release its memory resources.

        Idempotent: returns OK even if the instance is already archived
        (not in memory). When delete_log=True, also delete the JSONL log.

        When closing an active instance, take the per-instance lock to
        wait for any in-flight next call before releasing it safely.
        """
        maybe_sweep_terminal()
        log_path = state_dir / f"{instance_id}.jsonl"

        instance = store.get(instance_id)
        if instance is not None:
            async with instance.lock:
                # Another close may have removed it while we were waiting.
                still_present = store.get(instance_id)
                if still_present is not None:
                    store.remove(instance_id)
                    if still_present.event_log is not None:
                        still_present.event_log.close()
                    memo.clear_instance(instance_id)
                    logger.info(
                        "Archived instance: id=%s",
                        instance_id,
                    )

        if delete_log and log_path.exists():
            try:
                log_path.unlink()
                logger.info("Deleted JSONL log: %s", log_path.name)
            except OSError as e:
                logger.warning("Failed to delete JSONL log: %s (%s)", log_path, e)

        return {
            "ok": True,
            "state": "archived",
            "instance_id": instance_id,
            "log_deleted": delete_log and not log_path.exists(),
        }

    # ----------------------------------------------------------------
    # sequencer_list
    # ----------------------------------------------------------------
    @mcp.tool()
    async def sequencer_list(filter: str | None = None) -> dict[str, Any]:
        """Return the list of instances currently in memory (observation/debug).

        Args:
            filter: Status filter.
                "active":   only running / awaiting_result.
                "terminal": only completed / aborted / failed (in memory).
                "all":      everything (default).
                None is treated the same as "all".

        Returns:
            { "instances": [{ instance_id, program, state, step_no,
                              started_at, yielded_at }] }

        last_yield contents are not included; call sequencer_current for
        details. ARCHIVED instances (no longer in memory but still on disk)
        are not listed. To inspect ARCHIVED ones, look at state_dir on
        disk directly or try resume.
        """
        if filter not in (None, "active", "terminal", "all"):
            return _error(
                "InvalidArgument",
                f"filter must be one of active / terminal / all: got '{filter}'",
            )

        maybe_sweep_terminal()
        instances = store.list_all()
        if filter == "active":
            instances = [
                i for i in instances if not i.driver.is_terminal()
            ]
        elif filter == "terminal":
            instances = [i for i in instances if i.driver.is_terminal()]

        return {
            "instances": [
                {
                    "instance_id": inst.instance_id,
                    "program": inst.program_name,
                    "state": inst.state,
                    "step_no": inst.step_no,
                    "started_at": inst.started_at.isoformat(),
                    "yielded_at": (
                        inst.driver.yielded_at.isoformat()
                        if inst.driver.yielded_at is not None
                        else None
                    ),
                }
                for inst in instances
            ],
        }

    # ----------------------------------------------------------------
    # sequencer_memo_set / get / keys / delete
    #
    # Volatile in-memory KV scoped to one Instruction's execution. The
    # memo is cleared at the top of every sequencer_next (after the
    # for_step_no check), so cross-step retention is structurally
    # impossible. Use it for sub-agent IPC during the current
    # Instruction; persistent state belongs in files or in the
    # sequencer program's local variables.
    # ----------------------------------------------------------------
    @mcp.tool()
    async def sequencer_memo_set(
        instance_id: str, key: str, value: Any
    ) -> dict[str, Any]:
        """Store a value in the per-instance memo.

        The entry is dropped when the next sequencer_next call runs (or
        when the instance is closed / archived), so it cannot be used to
        carry data across steps. Intended for parallel sub-agents to
        exchange intermediate JSON during the execution of a single
        yielded Instruction without round-tripping through the
        orchestrator's context.
        """
        maybe_sweep_terminal()
        instance = store.get(instance_id)
        if instance is None:
            return _not_in_memory_response(state_dir, instance_id)
        try:
            size = await memo.set(instance_id, key, value)
        except InvalidMemoKey as e:
            return _error("InvalidArgument", str(e))
        except MemoQuotaExceeded as e:
            return _error("MemoQuotaExceeded", str(e))
        except TypeError as e:
            return _error(
                "InvalidArgument",
                f"value is not JSON-serializable: {e}",
            )
        return {"ok": True, "size": size}

    @mcp.tool()
    async def sequencer_memo_get(
        instance_id: str, key: str
    ) -> dict[str, Any]:
        """Read a value from the per-instance memo.

        Returns ``{"ok": true, "exists": false}`` when the key is
        missing, otherwise ``{"ok": true, "exists": true, "value": ...}``.
        """
        maybe_sweep_terminal()
        instance = store.get(instance_id)
        if instance is None:
            return _not_in_memory_response(state_dir, instance_id)
        try:
            value, exists = await memo.get(instance_id, key)
        except InvalidMemoKey as e:
            return _error("InvalidArgument", str(e))
        if not exists:
            return {"ok": True, "exists": False}
        return {"ok": True, "exists": True, "value": value}

    @mcp.tool()
    async def sequencer_memo_keys(
        instance_id: str, prefix: str | None = None
    ) -> dict[str, Any]:
        """List keys in the per-instance memo, optionally prefix-filtered.

        Keys are returned in sorted order.
        """
        maybe_sweep_terminal()
        instance = store.get(instance_id)
        if instance is None:
            return _not_in_memory_response(state_dir, instance_id)
        return {"ok": True, "keys": await memo.keys(instance_id, prefix)}

    @mcp.tool()
    async def sequencer_memo_delete(
        instance_id: str, key: str
    ) -> dict[str, Any]:
        """Delete a key from the per-instance memo.

        Returns ``{"ok": true, "deleted": true}`` when the key existed,
        ``{"ok": true, "deleted": false}`` otherwise.
        """
        maybe_sweep_terminal()
        instance = store.get(instance_id)
        if instance is None:
            return _not_in_memory_response(state_dir, instance_id)
        try:
            deleted = await memo.delete(instance_id, key)
        except InvalidMemoKey as e:
            return _error("InvalidArgument", str(e))
        return {"ok": True, "deleted": deleted}

    return mcp


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _instance_response(instance: Instance) -> dict[str, Any]:
    """Build the response shared by sequencer_current / next / start / resume."""
    response: dict[str, Any] = {
        "instance_id": instance.instance_id,
        "program": instance.program_name,
        "state": instance.state,
        "step_no": instance.step_no,
        "last_yield": instance.last_yield,
        "yielded_at": (
            instance.driver.yielded_at.isoformat()
            if instance.driver.yielded_at is not None
            else None
        ),
    }
    progress_hint = instance.driver.progress_hint
    if progress_hint is not None:
        response["progress_hint"] = progress_hint
    return response


def _not_in_memory_response(state_dir: Path, instance_id: str) -> dict[str, Any]:
    """Return the response for a query against an instance not in memory.

    NotLoaded (with a resume hint) when the JSONL log still exists,
    NotFound otherwise.
    """
    log_path = state_dir / f"{instance_id}.jsonl"
    if log_path.exists():
        return _error(
            "NotLoaded",
            f"Instance '{instance_id}' is not in memory (archived). "
            f"Use sequencer_resume to restore it. Log: {log_path.name}",
        )
    return _error(
        "NotFound",
        f"Instance '{instance_id}' not found",
    )


def _error(error_code: str, message: str) -> dict[str, Any]:
    """Build an error response."""
    return {"ok": False, "error": error_code, "message": message}
