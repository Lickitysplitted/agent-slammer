# 03 — Reuse one aiohttp.ClientSession per run

## Spec

### Problem
`request()` opens a fresh `aiohttp.ClientSession()` for every user-agent:

```python
async def request(target: str, agent: str) -> dict:
    ...
    async with aiohttp.ClientSession() as session:
        async with session.get(...) as req:
            ...
```

Across a UA list of hundreds, that is hundreds of sessions created and torn
down — one per request.

### Why per-request sessions are wrong
- **Connection pooling is defeated.** A `ClientSession` owns the `TCPConnector`
  and its keep-alive pool. A session per request means a new connector, a new
  TCP (and TLS) handshake every time, and nothing reused — the exact thing
  pooling exists to avoid.
- **Overhead.** Each session allocates a connector, cookie jar, and event-loop
  bookkeeping, then immediately discards them. aiohttp's own docs are explicit:
  create the session **once** and reuse it for the lifetime of the work.

### Acceptance criteria
- Exactly **one** `ClientSession` is constructed per `tasker()` run, regardless
  of agent count.
- `request()` **receives** a session; it does not create or close one.
- The session is created as an `async with` context in `tasker()` so it is
  cleanly closed after all requests complete.
- No new dependencies. Response shape and CSV output unchanged.

## Plan

Two edits, both native aiohttp, minimal diff.

### 1. `request()` — take a session, drop the `async with ClientSession()`
Signature gains `session`; the inner session-creation line is removed:

```python
async def request(target: str, agent: str, session: aiohttp.ClientSession) -> dict:
    if target and agent:
        async with session.get(target, headers={"user-agent": agent}) as req:
            reqdata = {
                "agent": agent,
                "url": target,
                "status code": req.status,
                "response headers": req.headers,
                "cookies": req.cookies,
                "response text": (await req.text()),
            }
        return reqdata
    else:
        logger.critical("CHECK-FAIL: Missing target URL and/or user agents")
```

### 2. `tasker()` — create the session once, pass it in
Wrap task creation in the session context and forward `session`:

```python
async def tasker(target: str, agents: json) -> List[dict]:
    if target and agents:
        reqtasks = []
        reqlog = []
        async with aiohttp.ClientSession() as session:
            for agent in track(agents, description="Building task list"):
                task = asyncio.create_task(
                    request(target=target, agent=agent["ua"], session=session)
                )
                reqtasks.append(task)
            for reqtask in track(reqtasks, description="Performing queries"):
                reqlog.append(await reqtask)
        return reqlog
    pass
```

The `async with` must stay open until every task is awaited (both loops inside
the block, as shown) — closing the session before the requests resolve would
cancel them.

### Resulting signatures
- `async def tasker(target: str, agents: json) -> List[dict]` — unchanged
  signature; body now owns the single session.
- `async def request(target: str, agent: str, session: aiohttp.ClientSession) -> dict`
  — gains `session`.

## Interaction note
This change edits the **same two functions** (`tasker`, `request`) as:
- the **redirects/errors** spec (adds error handling around `session.get`), and
- the **concurrency** spec (swaps the sequential await loop for
  `asyncio.gather`/a semaphore).

If these ship separately, coordinate ordering to avoid conflicting rewrites of
the same lines — land this one first, since a shared session is a prerequisite
for the others (gather over a single pooled session; timeouts/retries on that
session).

**ClientTimeout attaches on the session constructor.** When the errors spec
adds a timeout, it belongs here:
`aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=...))` — one timeout
config for the whole run, applied by the single session created in `tasker()`.

## Runnable check
Monkeypatch `aiohttp.ClientSession` to count instantiations and assert it is
constructed exactly once regardless of agent count. Save as `test_session_reuse.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import agent_slammer as slammer  # adjust import to how the module is loaded


def test_one_session_per_run():
    count = {"n": 0}

    def fake_session_factory(*args, **kwargs):
        count["n"] += 1
        resp = AsyncMock()
        resp.status = 200
        resp.headers = {}
        resp.cookies = {}
        resp.text = AsyncMock(return_value="ok")

        # session.get(...) returns an async context manager yielding resp
        get_cm = MagicMock()
        get_cm.__aenter__ = AsyncMock(return_value=resp)
        get_cm.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=get_cm)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        return session

    agents = [{"ua": f"UA-{i}"} for i in range(100)]
    with patch("aiohttp.ClientSession", side_effect=fake_session_factory):
        asyncio.run(slammer.tasker(target="http://example.test", agents=agents))

    assert count["n"] == 1, f"expected 1 session, got {count['n']}"


if __name__ == "__main__":
    test_one_session_per_run()
    print("ok: 1 session for 100 agents")
```

100 agents through `tasker()` must still yield exactly one `ClientSession`
construction. Before the change this asserts `100`; after, `1`.
