"""Runnable checks for agent-slammer. No framework, no network deps.

Run: python test_agent_slammer.py
"""
import asyncio
import importlib.util
import tempfile
from pathlib import Path

import aiohttp

# agent-slammer.py has a hyphen, so import it by path.
_spec = importlib.util.spec_from_file_location(
    "agent_slammer", Path(__file__).parent / "agent-slammer.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_error_row_populated():
    """A failed request still yields a uniform row with error set (spec 01)."""
    async def _run():
        sem = asyncio.Semaphore(1)
        timeout = aiohttp.ClientTimeout(total=1)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            return await m.request(s, sem, "http://127.0.0.1:1/", "test-ua", False)

    row = asyncio.run(_run())
    assert row["error"], "error column must be populated on failure"
    assert row["status code"] == "", "no status on a failed request"
    assert row["agent"] == "test-ua", "row must still identify its UA"
    assert set(m.REPORT_FIELDS) <= set(row), "every CSV column must have a key"


def test_reporter_single_header():
    """Reporter run twice against one file writes exactly one header (spec 04)."""
    rows = [{"agent": "a", "url": "u", "status code": "200"}]
    fd, name = tempfile.mkstemp(suffix=".csv")
    import os
    os.close(fd)
    p = Path(name)
    try:
        m.reporter(p, rows)
        m.reporter(p, rows)
        text = p.read_text(encoding="utf-8")
        header = ",".join(m.REPORT_FIELDS)
        assert text.count(header) == 1, f"expected 1 header, got {text.count(header)}"
        assert "request headers" not in text, "phantom column leaked"
    finally:
        p.unlink()


def test_concurrency_and_body_threading():
    """tasker caps in-flight requests (spec 05) and threads include_body (spec 06)."""
    state = {"cur": 0, "peak": 0, "bodies": set()}

    async def fake_request(session, sem, target, agent, include_body):
        state["bodies"].add(include_body)
        async with sem:
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
            await asyncio.sleep(0.01)
            state["cur"] -= 1
            return {"agent": agent}

    orig = m.request
    m.request = fake_request
    try:
        agents = [{"ua": f"ua{i}"} for i in range(50)]
        asyncio.run(m.tasker("http://x", agents, concurrency=3, timeout=10, include_body=True))
        assert state["peak"] == 3, f"peak concurrency was {state['peak']}, expected 3"
        assert state["bodies"] == {True}, "include_body must thread through to request()"
    finally:
        m.request = orig


if __name__ == "__main__":
    test_error_row_populated()
    test_reporter_single_header()
    test_concurrency_and_body_threading()
    print("ok")
