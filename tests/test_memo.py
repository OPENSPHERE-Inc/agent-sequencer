"""Unit tests for MemoStore."""

from __future__ import annotations

import asyncio

import pytest

from agent_sequencer.memo import (
    InvalidMemoKey,
    MemoQuotaExceeded,
    MemoStore,
)


@pytest.fixture
def store() -> MemoStore:
    return MemoStore()


# ----------------------------------------------------------------------
# set / get
# ----------------------------------------------------------------------
async def test_set_and_get_roundtrip(store: MemoStore) -> None:
    size = await store.set("inst-1", "key/a", {"x": 1, "y": [2, 3]})
    assert size > 0
    value, exists = await store.get("inst-1", "key/a")
    assert exists is True
    assert value == {"x": 1, "y": [2, 3]}


async def test_get_missing_key(store: MemoStore) -> None:
    value, exists = await store.get("inst-1", "key/missing")
    assert exists is False
    assert value is None


async def test_get_missing_instance(store: MemoStore) -> None:
    value, exists = await store.get("ghost", "key/a")
    assert exists is False
    assert value is None


async def test_overwrite_recomputes_size(store: MemoStore) -> None:
    await store.set("inst-1", "k", "small")
    await store.set("inst-1", "k", "x" * 1000)
    stats = store.stats("inst-1")
    assert stats["keys"] == 1
    # The total should reflect only the latest value, not old + new.
    expected = len(('"' + "x" * 1000 + '"').encode("utf-8"))
    assert stats["total_bytes"] == expected


async def test_value_types(store: MemoStore) -> None:
    await store.set("inst-1", "string", "hello")
    await store.set("inst-1", "number", 42)
    await store.set("inst-1", "bool", True)
    await store.set("inst-1", "null", None)
    await store.set("inst-1", "array", [1, 2, 3])
    await store.set("inst-1", "object", {"a": 1})
    for key, expected in [
        ("string", "hello"),
        ("number", 42),
        ("bool", True),
        ("null", None),
        ("array", [1, 2, 3]),
        ("object", {"a": 1}),
    ]:
        value, exists = await store.get("inst-1", key)
        assert exists is True
        assert value == expected


# ----------------------------------------------------------------------
# keys / delete
# ----------------------------------------------------------------------
async def test_keys_with_prefix(store: MemoStore) -> None:
    await store.set("inst-1", "round1/triage/C-1", "a")
    await store.set("inst-1", "round1/triage/M-1", "b")
    await store.set("inst-1", "round1/estimate/C-1", "c")
    keys = await store.keys("inst-1", prefix="round1/triage/")
    assert keys == ["round1/triage/C-1", "round1/triage/M-1"]


async def test_keys_no_prefix_returns_all_sorted(store: MemoStore) -> None:
    await store.set("inst-1", "b", 1)
    await store.set("inst-1", "a", 2)
    await store.set("inst-1", "c", 3)
    assert await store.keys("inst-1") == ["a", "b", "c"]


async def test_keys_empty_prefix_treated_as_no_prefix(store: MemoStore) -> None:
    await store.set("inst-1", "a", 1)
    await store.set("inst-1", "b", 2)
    assert await store.keys("inst-1", prefix="") == ["a", "b"]


async def test_keys_empty_instance(store: MemoStore) -> None:
    assert await store.keys("ghost") == []


async def test_delete_existing(store: MemoStore) -> None:
    await store.set("inst-1", "key/a", "v")
    assert await store.delete("inst-1", "key/a") is True
    _, exists = await store.get("inst-1", "key/a")
    assert exists is False


async def test_delete_missing(store: MemoStore) -> None:
    assert await store.delete("inst-1", "key/a") is False


async def test_delete_releases_quota(store: MemoStore) -> None:
    s = MemoStore(value_limit=200, instance_limit=300)
    await s.set("inst-1", "a", "x" * 100)
    await s.set("inst-1", "b", "y" * 100)
    # Without delete a third 100-byte value would push us over the
    # instance limit; deleting first should free up the space.
    with pytest.raises(MemoQuotaExceeded):
        await s.set("inst-1", "c", "z" * 100)
    assert await s.delete("inst-1", "a") is True
    await s.set("inst-1", "c", "z" * 100)


# ----------------------------------------------------------------------
# Quotas
# ----------------------------------------------------------------------
async def test_value_limit_exceeded() -> None:
    s = MemoStore(value_limit=100, instance_limit=10_000)
    with pytest.raises(MemoQuotaExceeded):
        await s.set("inst-1", "k", "x" * 200)


async def test_instance_limit_exceeded() -> None:
    s = MemoStore(value_limit=200, instance_limit=300)
    await s.set("inst-1", "a", "x" * 100)
    await s.set("inst-1", "b", "y" * 100)
    with pytest.raises(MemoQuotaExceeded):
        await s.set("inst-1", "c", "z" * 100)


async def test_overwrite_respects_instance_limit() -> None:
    s = MemoStore(value_limit=500, instance_limit=300)
    await s.set("inst-1", "a", "x" * 100)
    await s.set("inst-1", "b", "y" * 100)
    # Re-setting "a" with a larger value should account for the previous
    # size correctly. 500 - 100 + 250 = 650, far above 300, so it must fail.
    with pytest.raises(MemoQuotaExceeded):
        await s.set("inst-1", "a", "x" * 250)
    # The original "a" is preserved on quota failure.
    value, exists = await s.get("inst-1", "a")
    assert exists is True
    assert value == "x" * 100


def test_constructor_rejects_nonpositive_limits() -> None:
    with pytest.raises(ValueError):
        MemoStore(value_limit=0)
    with pytest.raises(ValueError):
        MemoStore(instance_limit=-1)


# ----------------------------------------------------------------------
# Bucket isolation
# ----------------------------------------------------------------------
async def test_separate_buckets(store: MemoStore) -> None:
    await store.set("a", "k", "from-a")
    await store.set("b", "k", "from-b")
    va, _ = await store.get("a", "k")
    vb, _ = await store.get("b", "k")
    assert va == "from-a"
    assert vb == "from-b"


async def test_clear_instance(store: MemoStore) -> None:
    await store.set("inst-1", "k", "v")
    store.clear_instance("inst-1")
    _, exists = await store.get("inst-1", "k")
    assert exists is False
    assert store.stats("inst-1") == {"keys": 0, "total_bytes": 0}


async def test_clear_other_instance_independent(store: MemoStore) -> None:
    await store.set("a", "k", 1)
    await store.set("b", "k", 2)
    store.clear_instance("a")
    _, exists_a = await store.get("a", "k")
    vb, exists_b = await store.get("b", "k")
    assert exists_a is False
    assert exists_b is True
    assert vb == 2


def test_clear_unknown_instance_is_noop(store: MemoStore) -> None:
    store.clear_instance("ghost")  # no exception
    assert store.stats("ghost") == {"keys": 0, "total_bytes": 0}


# ----------------------------------------------------------------------
# Key validation
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        " ",
        "has space",
        "tab\t",
        "上手",
        "a" * 257,
        "a@b",
        "a:b",
        "a?b",
    ],
)
async def test_invalid_keys_rejected(
    store: MemoStore, bad_key: str
) -> None:
    with pytest.raises(InvalidMemoKey):
        await store.set("inst-1", bad_key, "v")


@pytest.mark.parametrize(
    "good_key",
    [
        "a",
        "round1/triage/C-1",
        "k_1",
        "k.1",
        "k-1",
        "a/b/c/d",
        "x" * 256,
    ],
)
async def test_valid_keys_accepted(
    store: MemoStore, good_key: str
) -> None:
    await store.set("inst-1", good_key, "v")
    _, exists = await store.get("inst-1", good_key)
    assert exists is True


async def test_invalid_key_on_get(store: MemoStore) -> None:
    with pytest.raises(InvalidMemoKey):
        await store.get("inst-1", "bad key")


async def test_invalid_key_on_delete(store: MemoStore) -> None:
    with pytest.raises(InvalidMemoKey):
        await store.delete("inst-1", "bad key")


async def test_non_string_key_rejected(store: MemoStore) -> None:
    with pytest.raises(InvalidMemoKey):
        await store.set("inst-1", 42, "v")  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Concurrency
# ----------------------------------------------------------------------
async def test_concurrent_set(store: MemoStore) -> None:
    async def writer(i: int) -> None:
        await store.set("inst-1", f"k-{i}", i)

    await asyncio.gather(*(writer(i) for i in range(50)))
    assert store.stats("inst-1")["keys"] == 50
    for i in range(50):
        value, exists = await store.get("inst-1", f"k-{i}")
        assert exists is True
        assert value == i


async def test_concurrent_overwrite_keeps_consistent_total() -> None:
    s = MemoStore()

    async def writer(payload: str) -> None:
        await s.set("inst-1", "k", payload)

    payloads = ["a" * n for n in range(1, 30)]
    await asyncio.gather(*(writer(p) for p in payloads))
    stats = s.stats("inst-1")
    assert stats["keys"] == 1
    # Whichever payload won the race, the total must equal the encoded
    # size of the value currently stored — never a stale running sum.
    value, _ = await s.get("inst-1", "k")
    expected = len(('"' + value + '"').encode("utf-8"))
    assert stats["total_bytes"] == expected


# ----------------------------------------------------------------------
# stats helper
# ----------------------------------------------------------------------
async def test_stats_reflects_writes(store: MemoStore) -> None:
    assert store.stats("inst-1") == {"keys": 0, "total_bytes": 0}
    await store.set("inst-1", "a", "hello")
    after = store.stats("inst-1")
    assert after["keys"] == 1
    assert after["total_bytes"] == len('"hello"'.encode("utf-8"))


def test_stats_unknown_instance(store: MemoStore) -> None:
    assert store.stats("ghost") == {"keys": 0, "total_bytes": 0}
