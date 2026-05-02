"""Unit tests for the JSONL EventLog and prune_old_logs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from agent_sequencer.persistence import (
    DISK_TTL_COMPLETED,
    DISK_TTL_FAILED,
    EventLog,
    header_event,
    prune_old_logs,
    read_events,
    yield_event,
)


def _now(offset: timedelta = timedelta()) -> datetime:
    return datetime.now(tz=timezone.utc) + offset


def test_event_log_appends_and_reads_back(state_dir):
    path = state_dir / "abc.jsonl"
    log = EventLog(path)
    log.append(header_event(
        instance_id="abc",
        program="hello",
        source_hash="deadbeef",
        params={},
        started_at=_now(),
    ))
    log.append(yield_event(
        step_no=1,
        payload={"kind": "instruction", "text": "hi"},
        yielded_at=_now(),
    ))
    log.close()

    events = read_events(path)
    assert len(events) == 2
    assert events[0]["type"] == "header"
    assert events[1]["type"] == "yield"
    assert events[1]["step_no"] == 1


def test_event_log_partial_construction_safe(tmp_path):
    """__del__ must not raise AttributeError when __init__ fails."""
    bad_path = tmp_path / "nonexistent" / "abc.jsonl"
    try:
        EventLog(bad_path)
    except (FileNotFoundError, OSError):
        pass


def _write_log(path, started_at, last_yield_payload, last_yielded_at):
    with path.open("w", encoding="utf-8") as fp:
        fp.write(json.dumps(header_event(
            instance_id=path.stem,
            program="hello",
            source_hash="x",
            params={},
            started_at=started_at,
        )) + "\n")
        fp.write(json.dumps(yield_event(
            step_no=1,
            payload=last_yield_payload,
            yielded_at=last_yielded_at,
        )) + "\n")


def test_prune_old_logs_removes_expired_completed(state_dir):
    """A JSONL completed more than DISK_TTL_COMPLETED + 1 day ago is deleted."""
    path = state_dir / "old.jsonl"
    expired_at = _now(-DISK_TTL_COMPLETED - timedelta(days=1))
    _write_log(path, expired_at, {"kind": "done", "summary": {}}, expired_at)
    pruned = prune_old_logs(state_dir)
    assert pruned == 1
    assert not path.exists()


def test_prune_old_logs_keeps_fresh(state_dir):
    """A JSONL completed just now is kept."""
    path = state_dir / "fresh.jsonl"
    fresh_at = _now()
    _write_log(path, fresh_at, {"kind": "done", "summary": {}}, fresh_at)
    pruned = prune_old_logs(state_dir)
    assert pruned == 0
    assert path.exists()


def test_prune_old_logs_keeps_failed_longer(state_dir):
    """failed (= kind=error) is retained until DISK_TTL_FAILED."""
    path = state_dir / "failed.jsonl"
    age = _now(-DISK_TTL_COMPLETED - timedelta(days=1))
    _write_log(path, age, {"kind": "error", "reason": "boom"}, age)
    pruned = prune_old_logs(state_dir)
    assert pruned == 0  # error is retained for up to 90 days
    assert path.exists()
    assert DISK_TTL_FAILED > DISK_TTL_COMPLETED  # spec sanity check
