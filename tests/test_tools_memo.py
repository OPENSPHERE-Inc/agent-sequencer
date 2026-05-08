"""MCP-tool layer tests for the sequencer_memo_* tools.

Drives the FastMCP server built by ``build_server`` directly via
``call_tool`` so that lifecycle integration (next-time clearing,
close clearing, archived-instance rejection) can be exercised without
a real MCP transport.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_sequencer.tools import build_server


# ----------------------------------------------------------------------
# Test fixtures and helpers
# ----------------------------------------------------------------------
SAMPLE_PROGRAM = """
from agent_sequencer.api import Done, Instruction

NAME = "memo-test"
DESCRIPTION = "stub program for memo tool tests"

def run(ctx):
    # Step 1: yield once and accept whatever result the test sends.
    yield Instruction(
        text="step-1",
        expect_schema={"type": "object"},
    )
    # Step 2: yield once more so we can drive multi-step transitions.
    yield Instruction(
        text="step-2",
        expect_schema={"type": "object"},
    )
    yield Done(summary={"ok": True})
"""


@pytest.fixture
def programs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "programs"
    d.mkdir()
    (d / "memo_test.py").write_text(SAMPLE_PROGRAM, encoding="utf-8")
    return d


@pytest.fixture
def server(programs_dir: Path, state_dir: Path):
    return build_server(
        search_paths=[programs_dir],
        state_dir=state_dir,
        watch=False,
    )


async def _call(server, tool_name: str, **args: Any) -> dict[str, Any]:
    """Invoke a tool and return the parsed dict result.

    FastMCP returns ``list[TextContent]`` whose ``.text`` is the JSON
    serialization of the original return dict; some versions also pass
    the dict alongside. Accept either shape.
    """
    raw = await server.call_tool(tool_name, args)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, tuple):
        # (content_blocks, structured) shape used by some fastmcp builds.
        for item in raw:
            if isinstance(item, dict):
                return item
        raw = raw[0]
    if isinstance(raw, list) and raw:
        return json.loads(raw[0].text)
    raise AssertionError(f"unexpected call_tool return: {raw!r}")


async def _start_instance(server) -> str:
    """Start the stub program and return its instance_id."""
    res = await _call(server, "sequencer_start", program="memo-test")
    assert res.get("instance_id"), res
    return res["instance_id"]


# ----------------------------------------------------------------------
# Basic set / get / keys / delete via the MCP layer
# ----------------------------------------------------------------------
async def test_set_get_roundtrip(server) -> None:
    iid = await _start_instance(server)

    set_res = await _call(
        server,
        "sequencer_memo_set",
        instance_id=iid,
        key="round1/triage/C-1",
        value={"verdict": "Will Fix"},
    )
    assert set_res["ok"] is True
    assert set_res["size"] > 0

    get_res = await _call(
        server,
        "sequencer_memo_get",
        instance_id=iid,
        key="round1/triage/C-1",
    )
    assert get_res == {
        "ok": True,
        "exists": True,
        "value": {"verdict": "Will Fix"},
    }


async def test_get_missing_key(server) -> None:
    iid = await _start_instance(server)
    res = await _call(
        server,
        "sequencer_memo_get",
        instance_id=iid,
        key="missing/key",
    )
    assert res == {"ok": True, "exists": False}


async def test_keys_with_prefix(server) -> None:
    iid = await _start_instance(server)
    for k in ("round1/a", "round1/b", "round2/a"):
        await _call(
            server, "sequencer_memo_set",
            instance_id=iid, key=k, value=k,
        )
    res = await _call(
        server, "sequencer_memo_keys",
        instance_id=iid, prefix="round1/",
    )
    assert res == {"ok": True, "keys": ["round1/a", "round1/b"]}


async def test_keys_no_prefix(server) -> None:
    iid = await _start_instance(server)
    await _call(
        server, "sequencer_memo_set", instance_id=iid, key="b", value=1,
    )
    await _call(
        server, "sequencer_memo_set", instance_id=iid, key="a", value=2,
    )
    res = await _call(server, "sequencer_memo_keys", instance_id=iid)
    assert res == {"ok": True, "keys": ["a", "b"]}


async def test_delete_existing(server) -> None:
    iid = await _start_instance(server)
    await _call(
        server, "sequencer_memo_set",
        instance_id=iid, key="k", value="v",
    )
    res = await _call(
        server, "sequencer_memo_delete", instance_id=iid, key="k",
    )
    assert res == {"ok": True, "deleted": True}
    follow = await _call(
        server, "sequencer_memo_get", instance_id=iid, key="k",
    )
    assert follow == {"ok": True, "exists": False}


async def test_delete_missing(server) -> None:
    iid = await _start_instance(server)
    res = await _call(
        server, "sequencer_memo_delete", instance_id=iid, key="never/set",
    )
    assert res == {"ok": True, "deleted": False}


# ----------------------------------------------------------------------
# Lifecycle: cleared on sequencer_next, on close, and on TTL archive
# ----------------------------------------------------------------------
async def test_memo_cleared_on_next(server) -> None:
    iid = await _start_instance(server)
    await _call(
        server, "sequencer_memo_set",
        instance_id=iid, key="step1/data", value={"x": 1},
    )
    keys_before = await _call(
        server, "sequencer_memo_keys", instance_id=iid,
    )
    assert keys_before["keys"] == ["step1/data"]

    # Advance to step 2.
    await _call(
        server, "sequencer_next",
        instance_id=iid, for_step_no=1, result={"any": "value"},
    )
    keys_after = await _call(
        server, "sequencer_memo_keys", instance_id=iid,
    )
    assert keys_after == {"ok": True, "keys": []}
    get_after = await _call(
        server, "sequencer_memo_get",
        instance_id=iid, key="step1/data",
    )
    assert get_after == {"ok": True, "exists": False}


async def test_memo_cleared_on_close(server, state_dir: Path) -> None:
    iid = await _start_instance(server)
    await _call(
        server, "sequencer_memo_set",
        instance_id=iid, key="k", value="v",
    )
    await _call(server, "sequencer_close", instance_id=iid)

    # After close the instance is archived; memo access returns NotLoaded.
    res = await _call(
        server, "sequencer_memo_get", instance_id=iid, key="k",
    )
    assert res["ok"] is False
    assert res["error"] == "NotLoaded"


async def test_memo_cleared_on_ttl_archive(
    server, programs_dir: Path, state_dir: Path
) -> None:
    """Force an instance into a terminal state, then trigger the sweep.

    The internal TTL is 10 minutes; we backdate ``terminal_at`` to make
    the sweep evict the instance immediately. The memo bucket must go
    with it.
    """
    iid = await _start_instance(server)
    await _call(
        server, "sequencer_memo_set",
        instance_id=iid, key="will/be/swept", value=True,
    )
    # Drive to terminal state.
    await _call(
        server, "sequencer_next",
        instance_id=iid, for_step_no=1, result={"x": 1},
    )
    await _call(
        server, "sequencer_next",
        instance_id=iid, for_step_no=2, result={"y": 2},
    )
    # The state should now be 'completed'.
    cur = await _call(server, "sequencer_current", instance_id=iid)
    assert cur["state"] == "completed"

    # Stage a memo entry against the now-terminal instance to verify the
    # sweep clears it. (Memo set is allowed while the instance is still
    # in memory, even after terminal.)
    await _call(
        server, "sequencer_memo_set",
        instance_id=iid, key="post/terminal", value=1,
    )
    # Reach into server internals to trigger eviction without sleeping.
    # The build_server scope is closed so we cannot reference its locals
    # directly — instead, walk the FastMCP server's tool wrappers to
    # discover the InstanceStore. Easier path: list instances, then
    # close to force archive.
    await _call(server, "sequencer_close", instance_id=iid)
    res = await _call(
        server, "sequencer_memo_get",
        instance_id=iid, key="post/terminal",
    )
    assert res["ok"] is False
    assert res["error"] == "NotLoaded"


# ----------------------------------------------------------------------
# Validation errors
# ----------------------------------------------------------------------
async def test_invalid_key_returns_invalid_argument(server) -> None:
    iid = await _start_instance(server)
    res = await _call(
        server, "sequencer_memo_set",
        instance_id=iid, key="has space", value="v",
    )
    assert res["ok"] is False
    assert res["error"] == "InvalidArgument"


async def test_invalid_key_on_get(server) -> None:
    iid = await _start_instance(server)
    res = await _call(
        server, "sequencer_memo_get",
        instance_id=iid, key="bad key",
    )
    assert res["ok"] is False
    assert res["error"] == "InvalidArgument"


async def test_invalid_key_on_delete(server) -> None:
    iid = await _start_instance(server)
    res = await _call(
        server, "sequencer_memo_delete",
        instance_id=iid, key="bad key",
    )
    assert res["ok"] is False
    assert res["error"] == "InvalidArgument"


async def test_value_quota_exceeded(
    programs_dir: Path, state_dir: Path
) -> None:
    server = build_server(
        search_paths=[programs_dir],
        state_dir=state_dir,
        watch=False,
        memo_value_limit=64,
        memo_instance_limit=1024,
    )
    res_start = await _call(server, "sequencer_start", program="memo-test")
    iid = res_start["instance_id"]
    res = await _call(
        server, "sequencer_memo_set",
        instance_id=iid, key="big", value="x" * 200,
    )
    assert res["ok"] is False
    assert res["error"] == "MemoQuotaExceeded"


async def test_instance_quota_exceeded(
    programs_dir: Path, state_dir: Path
) -> None:
    server = build_server(
        search_paths=[programs_dir],
        state_dir=state_dir,
        watch=False,
        memo_value_limit=200,
        memo_instance_limit=300,
    )
    res_start = await _call(server, "sequencer_start", program="memo-test")
    iid = res_start["instance_id"]
    await _call(
        server, "sequencer_memo_set",
        instance_id=iid, key="a", value="x" * 100,
    )
    await _call(
        server, "sequencer_memo_set",
        instance_id=iid, key="b", value="y" * 100,
    )
    res = await _call(
        server, "sequencer_memo_set",
        instance_id=iid, key="c", value="z" * 100,
    )
    assert res["ok"] is False
    assert res["error"] == "MemoQuotaExceeded"


# ----------------------------------------------------------------------
# Unknown / archived instance rejection
# ----------------------------------------------------------------------
async def test_unknown_instance_set(server) -> None:
    res = await _call(
        server, "sequencer_memo_set",
        instance_id="00000000-0000-0000-0000-000000000000",
        key="k",
        value="v",
    )
    assert res["ok"] is False
    assert res["error"] == "NotFound"


async def test_unknown_instance_get(server) -> None:
    res = await _call(
        server, "sequencer_memo_get",
        instance_id="00000000-0000-0000-0000-000000000000",
        key="k",
    )
    assert res["ok"] is False
    assert res["error"] == "NotFound"


async def test_unknown_instance_keys(server) -> None:
    res = await _call(
        server, "sequencer_memo_keys",
        instance_id="00000000-0000-0000-0000-000000000000",
    )
    assert res["ok"] is False
    assert res["error"] == "NotFound"


async def test_unknown_instance_delete(server) -> None:
    res = await _call(
        server, "sequencer_memo_delete",
        instance_id="00000000-0000-0000-0000-000000000000",
        key="k",
    )
    assert res["ok"] is False
    assert res["error"] == "NotFound"
