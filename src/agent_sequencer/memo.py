"""In-memory key-value store scoped to a single Instruction's execution.

The memo store is volatile and cleared on every sequencer_next call, so
its contents only ever survive within the execution window of a single
yielded Instruction. This is by design:

  - Cross-step retention is structurally impossible, which keeps the
    sequencer_resume code path trivially consistent: a freshly resumed
    Driver and a never-archived Driver both see an empty memo before the
    next Instruction runs.
  - No EventLog persistence is required (and none is performed). The
    memo is therefore not part of the deterministic-replay contract.
  - The intended use case is sub-agent IPC during the execution of one
    Instruction: parallel sub-agents stash intermediate JSON values
    here so the orchestrator does not have to round-trip them through
    its own context.

Buckets are keyed by ``instance_id``. Quotas guard against runaway
sub-agents inflating server memory.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Default per-value and per-instance size quotas. Overridable from
# __main__.py via the AGENT_SEQUENCER_MEMO_* environment variables.
DEFAULT_VALUE_LIMIT = 1024 * 1024  # 1 MiB
DEFAULT_INSTANCE_LIMIT = 64 * 1024 * 1024  # 64 MiB

# Allowed key syntax: ASCII alnum plus underscore / dot / dash / slash.
# Slashes are intended for hierarchical keys (e.g. ``round1/triage/C-1``).
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_./-]{1,256}$")


class InvalidMemoKey(ValueError):
    """The supplied key does not match KEY_PATTERN."""


class MemoQuotaExceeded(ValueError):
    """A set would exceed the per-value or per-instance quota."""


@dataclass
class _Bucket:
    """One instance's memo bucket."""

    data: dict[str, Any] = field(default_factory=dict)
    sizes: dict[str, int] = field(default_factory=dict)
    total: int = 0


class MemoStore:
    """Per-instance volatile KV store.

    All async methods serialize through a single store-wide
    ``asyncio.Lock`` so concurrent set / get / keys / delete calls observe
    a consistent view. ``clear_instance`` is synchronous because it is
    called from lifecycle hooks (sequencer_next, TTL sweep) that may not
    own the event loop. Bucket replacement via ``dict.pop`` is atomic
    under the GIL so no lock is needed there.
    """

    def __init__(
        self,
        value_limit: int = DEFAULT_VALUE_LIMIT,
        instance_limit: int = DEFAULT_INSTANCE_LIMIT,
    ) -> None:
        if value_limit <= 0:
            raise ValueError("value_limit must be positive")
        if instance_limit <= 0:
            raise ValueError("instance_limit must be positive")
        self._buckets: dict[str, _Bucket] = {}
        self._value_limit = value_limit
        self._instance_limit = instance_limit
        self._lock = asyncio.Lock()

    @property
    def value_limit(self) -> int:
        return self._value_limit

    @property
    def instance_limit(self) -> int:
        return self._instance_limit

    @staticmethod
    def validate_key(key: Any) -> None:
        """Raise ``InvalidMemoKey`` if the key does not match KEY_PATTERN."""
        if not isinstance(key, str) or not KEY_PATTERN.match(key):
            raise InvalidMemoKey(
                f"key must match {KEY_PATTERN.pattern!r}: got {key!r}"
            )

    @staticmethod
    def _encoded_size(value: Any) -> int:
        """Return the UTF-8 byte size of the JSON encoding of ``value``."""
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))

    async def set(self, instance_id: str, key: str, value: Any) -> int:
        """Store a value. Returns the encoded UTF-8 byte size.

        Raises:
            InvalidMemoKey: ``key`` does not match KEY_PATTERN.
            MemoQuotaExceeded: ``value`` exceeds ``value_limit`` or the
                resulting bucket total would exceed ``instance_limit``.
            TypeError: ``value`` is not JSON-serializable.
        """
        self.validate_key(key)
        size = self._encoded_size(value)
        if size > self._value_limit:
            raise MemoQuotaExceeded(
                f"value size {size} exceeds per-value limit "
                f"{self._value_limit}"
            )
        async with self._lock:
            bucket = self._buckets.setdefault(instance_id, _Bucket())
            old_size = bucket.sizes.get(key, 0)
            new_total = bucket.total - old_size + size
            if new_total > self._instance_limit:
                raise MemoQuotaExceeded(
                    f"instance total {new_total} would exceed limit "
                    f"{self._instance_limit}"
                )
            bucket.data[key] = value
            bucket.sizes[key] = size
            bucket.total = new_total
        return size

    async def get(self, instance_id: str, key: str) -> tuple[Any, bool]:
        """Return ``(value, exists)``. ``value`` is None when missing."""
        self.validate_key(key)
        async with self._lock:
            bucket = self._buckets.get(instance_id)
            if bucket is None or key not in bucket.data:
                return None, False
            return bucket.data[key], True

    async def keys(
        self, instance_id: str, prefix: str | None = None
    ) -> list[str]:
        """List keys in the instance bucket, optionally prefix-filtered.

        Keys are returned in sorted order so callers can rely on a
        deterministic iteration sequence.
        """
        async with self._lock:
            bucket = self._buckets.get(instance_id)
            if bucket is None:
                return []
            if not prefix:
                return sorted(bucket.data.keys())
            return sorted(k for k in bucket.data if k.startswith(prefix))

    async def delete(self, instance_id: str, key: str) -> bool:
        """Delete a key. Returns ``True`` if the key existed."""
        self.validate_key(key)
        async with self._lock:
            bucket = self._buckets.get(instance_id)
            if bucket is None or key not in bucket.data:
                return False
            size = bucket.sizes.pop(key)
            del bucket.data[key]
            bucket.total -= size
            if not bucket.data:
                self._buckets.pop(instance_id, None)
            return True

    def clear_instance(self, instance_id: str) -> None:
        """Drop the entire bucket for ``instance_id``.

        Synchronous so it can be called from sequencer_next (already
        holding ``instance.lock``) and from the TTL sweep (synchronous
        context). Atomic under the GIL.
        """
        self._buckets.pop(instance_id, None)

    def stats(self, instance_id: str) -> dict[str, int]:
        """Return ``{keys, total_bytes}`` for the instance (debug helper)."""
        bucket = self._buckets.get(instance_id)
        if bucket is None:
            return {"keys": 0, "total_bytes": 0}
        return {"keys": len(bucket.data), "total_bytes": bucket.total}
