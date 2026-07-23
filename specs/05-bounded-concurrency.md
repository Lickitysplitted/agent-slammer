# 05 — Bounded Concurrency

## Spec

### Problem
`tasker()` calls `asyncio.create_task(request(...))` once per user-agent in a
loop, then awaits them. `create_task` schedules the coroutine to run
*immediately* on the event loop — so by the time the first `await reqtask`
runs, every request is already in flight. With 50 UAs that's fine. With 500+
it means 500+ simultaneous sockets and 500+ near-simultaneous GETs at the
target.

### Why it matters here
- **The target is usually the user's own service under test.** "Rapid" is the
  point; "flood" is not. 500 concurrent hits looks like a burst-load event, not
  a browser/device validation run.
- **Local socket limits.** Hundreds of simultaneous outbound connections can
  exhaust ephemeral ports / file descriptors and cause spurious connection
  errors that look like target failures but aren't.
- The tool's job is to *walk* a UA list against a target, not to see how hard it
  can push it.

### Acceptance criteria
1. No more than **N** requests are in flight (network call active) at any instant.
2. N is configurable via a `--concurrency` CLI option.
3. Default is **20**, documented in the option help text.
4. All requests still complete; results order/content unchanged.

## Plan

Minimal, stdlib `asyncio` only. No new deps.

### Where to gate
Acquire the semaphore **inside `request()`**, wrapping the actual `aiohttp`
GET, not in `tasker()` around task creation.

Justification: `create_task` starts the coroutine immediately, so guarding
task *creation* does nothing — all coroutines are already scheduled. The
semaphore must sit around the awaited network call so a coroutine blocks there
until a slot frees. Putting `async with sem:` in `request()` is the smallest
correct place: the guarded region is exactly the I/O we want to bound, and
`tasker()` keeps its simple create-all-then-await-all shape.

### Code changes

**1. `slam()` — add the option and thread it through**
```python
concurrency: int = typer.Option(
    20, "--concurrency",
    help="Max requests in flight at once (default 20)",
),
```
Pass to tasker: `asyncio.run(tasker(url, agents_data, concurrency))`.

**2. `tasker()` — create one semaphore, pass it to each request**
```python
async def tasker(target: str, agents: json, concurrency: int) -> List[dict]:
    if target and agents:
        sem = asyncio.Semaphore(concurrency)
        reqtasks = []
        reqlog = []
        for agent in track(agents, description="Building task list"):
            task = asyncio.create_task(request(target=target, agent=agent["ua"], sem=sem))
            reqtasks.append(task)
        for reqtask in track(reqtasks, description="Performing queries"):
            reqlog.append(await reqtask)
        return reqlog
```

**3. `request()` — accept `sem`, wrap the GET**
```python
async def request(target: str, agent: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        ...  # existing aiohttp GET, unchanged inside the block
```

That's the whole change: one option, one `Semaphore(...)`, one param threaded
`slam → tasker → request`, one `async with`.

## Interaction note
`tasker` and `request` are the **same two functions** touched by the
session-reuse spec and the redirects/errors (timeout) spec. Coordinate the
signatures so they land together instead of colliding — the end state for
`request()` is something like:

```python
async def request(target, agent, sem, session, timeout) -> dict: ...
```

with `sem`, the shared `aiohttp.ClientSession`, and the `ClientTimeout` all
threaded from `slam → tasker → request` in one pass. Whoever lands last should
merge, not re-add, the parameters. The `async with sem:` here composes cleanly
inside the shared-session block from the session spec.

## Runnable check
No network. Prove peak concurrency never exceeds the limit with a fake
`request` and a shared counter.

```python
# test_concurrency.py
import asyncio

async def bounded_gets(concurrency, n):
    sem = asyncio.Semaphore(concurrency)
    live = 0
    peak = 0
    async def fake_request(i):
        nonlocal live, peak
        async with sem:
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.01)   # hold the slot so overlap is real
            live -= 1
        return i
    await asyncio.gather(*(fake_request(i) for i in range(n)))
    return peak

def test_peak_never_exceeds_limit():
    peak = asyncio.run(bounded_gets(concurrency=3, n=50))
    assert peak <= 3, f"peak concurrency {peak} exceeded limit 3"
    assert peak == 3, f"expected saturation at 3, got {peak}"

if __name__ == "__main__":
    test_peak_never_exceeds_limit()
    print("ok")
```

`live`/`peak` mirror the real semaphore-guarded region: increment on enter,
`sleep` to force overlap, decrement on exit. `peak <= 3` is the bound;
`peak == 3` confirms the limit isn't accidentally serializing everything.
