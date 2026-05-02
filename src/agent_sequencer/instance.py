"""Instance state management.

Holds per-instance state (Driver, metadata, lifecycle status) keyed by
instance_id. Each instance carries a per-instance asyncio.Lock to serialize
concurrent sequencer_next calls against the same instance_id.

Each instance owns an EventLog (an append-only JSONL handle); events are
persisted on start / next. resume reconstructs state from this log.

TTL/close support is planned for step 6.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .persistence import EventLog
from .runtime import Driver

logger = logging.getLogger(__name__)


@dataclass
class Instance:
    """One sequencer execution.

    Attributes:
        instance_id:  UUID. The identifier the agent uses for every
                      sequencer_* call.
        program_name: Name of the program that was started.
        params:       The params supplied to sequencer_start.
        driver:       The generator driver. The state lives in driver.state.
        started_at:   Instance start time (UTC).
        lock:         asyncio.Lock that serializes concurrent next calls
                      against the same instance. current is read-only and
                      does not take the lock.
        event_log:    Append-only JSONL handle. None disables persistence
                      (for testing).
    """

    instance_id: str
    program_name: str
    params: dict[str, Any]
    driver: Driver
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
    event_log: EventLog | None = field(default=None, repr=False, compare=False)

    @property
    def state(self) -> str:
        return self.driver.state

    @property
    def step_no(self) -> int:
        return self.driver.step_no

    @property
    def last_yield(self) -> dict[str, Any] | None:
        return self.driver.last_yield


class InstanceStore:
    """In-memory store of Instance objects keyed by instance_id."""

    def __init__(self) -> None:
        self._instances: dict[str, Instance] = {}

    def add(self, instance: Instance) -> None:
        """Register an already-constructed Instance into the store.

        Used both for instances restored from JSONL during resume and for
        instances that the tools layer has built with an ID and a log.
        """
        if instance.instance_id in self._instances:
            raise RuntimeError(f"instance_id collision: {instance.instance_id}")
        self._instances[instance.instance_id] = instance
        logger.info(
            "Registered instance: id=%s program=%s",
            instance.instance_id,
            instance.program_name,
        )

    def new_instance_id(self) -> str:
        """Return a fresh UUID for a new instance (collisions are effectively impossible)."""
        return str(uuid.uuid4())

    def get(self, instance_id: str) -> Instance | None:
        """Return the instance with the given id, or None if missing."""
        return self._instances.get(instance_id)

    def remove(self, instance_id: str) -> Instance | None:
        """Remove the instance from the store. Returns None if missing."""
        return self._instances.pop(instance_id, None)

    def list_all(self) -> list[Instance]:
        """Return all instances as a list."""
        return list(self._instances.values())
