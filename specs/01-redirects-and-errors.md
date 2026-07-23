# 01 — Redirects & Per-Request Errors

## Spec

### Problem
`agent-slammer` fires a GET per user-agent and records `req.status` plus headers/cookies/body. Two gaps:

1. **No redirect evidence.** aiohttp follows redirects by default, so `req.status` is the *final* status and the recorded `url` is the *requested* URL. If a mobile UA is redirected to `m.site.com` or an unsupported UA to `/not-supported`, the CSV looks identical to a UA that got served in-place at 200. The redirect *is* the device-flow signal, and right now it's invisible.
2. **No error isolation or timeout.** `request()` has no try/except and no timeout. A dead endpoint hangs the run forever (no timeout); a single `aiohttp.ClientError` raised at `await reqtask` in `tasker()` crashes the whole run and discards *every* result already gathered.

### Why it matters for device-flow validation
The entire point of the tool is confirming "UA X lands on flow Y". Without the final URL and redirect chain there's no way to assert that. Without per-request error handling, one flaky UA (or one slow origin) either loses the whole report or makes it appear the tool "did nothing" — false negatives in a validation tool are worse than useless.

### Acceptance criteria
Each row already has: `agent`, `url`, `status code`, `response headers`, `cookies`, `response text`. Add `final url`, `redirect chain`, `error`. Then:

- **Redirected request** (UA → `m.site.com`): row has `status code` = final status (e.g. 200), `final url` = `https://m.site.com/...`, `redirect chain` = `https://site.com/... -> https://m.site.com/...` (the pre-final URLs, `" -> "`-joined), `error` empty.
- **Non-redirected request**: `final url` = requested `url`, `redirect chain` empty, `error` empty.
- **Timed-out request**: a row still exists for that UA; `status code`, `final url`, `redirect chain`, body/headers/cookies empty; `error` populated, e.g. `TimeoutError: ` (or `asyncio.TimeoutError`, whichever aiohttp surfaces). The run does **not** hang past the timeout and does **not** abort other UAs.
- **Connection error** (bad host, refused): row exists; `error` populated, e.g. `ClientConnectorError: Cannot connect to host ...`; other UAs unaffected.

## Plan

Minimal, aiohttp-native, no new deps, no new abstractions.

### `request()` — rewrite the body
- Add a `timeout: float` parameter (seconds).
- Build the result dict up front with **all** keys present and empty defaults so every row is uniform (DictWriter needs every key present):
  ```
  reqdata = {agent, url, "status code": "", "final url": "", "redirect chain": "",
             "response headers": "", "cookies": "", "response text": "", "error": ""}
  ```
- Wrap the session/get in `try/except Exception as exc:`. On success, fill:
  - `"status code": req.status`
  - `"final url": str(req.url)`
  - `"redirect chain": " -> ".join(str(h.url) for h in req.history)`
  - `"response headers"`, `"cookies"`, `"response text"` as today.
- On failure: `reqdata["error"] = f"{type(exc).__name__}: {exc}"` and `logger.warning(...)`. Catch broad `Exception` on purpose — a validation run must never lose a row; `ponytail:` comment marks it.
- Pass the timeout to aiohttp: `aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout))`. This covers the whole request incl. redirect following, so a slow chain also trips it.
- Return `reqdata` in all paths (drop the `else: logger.critical` — the missing-inputs guard stays as an early check that returns the empty dict, or keep the existing `if target and agent` and just return the error-populated dict).

### `tasker()` — thread the timeout
- Add `timeout: float` param; pass to `request(...)`.
- `await reqtask` can no longer raise (request swallows its own errors), so the gather loop is now crash-safe as-is. No other change.

### `reporter()` — keep fieldnames in sync
The `DictWriter` fieldnames list is the source of truth and **must** match the dict keys exactly (DictWriter raises `ValueError` on extra keys). Update it to:
```
["agent", "url", "status code", "final url", "redirect chain",
 "response headers", "cookies", "response text", "error"]
```
Note: the current list contains `"request headers"`, which no dict ever sets (it writes blank). Leave it or drop it — out of scope; do not add data for it. Just make sure every key `request()` emits appears here.

### `slam()` — CLI
Add one option:
```
timeout: float = typer.Option(10.0, help="Per-request timeout in seconds"),
```
Thread it: `asyncio.run(tasker(target=url, agents=agents, timeout=timeout))`. Default 10s — a real device-flow endpoint answers fast; 10s is generous without hanging a run on one dead UA.

### Not doing (YAGNI)
- No retry logic, no per-attempt backoff, no separate redirect-disable mode. If someone later needs the raw 3xx statuses without following, add `allow_redirects=False` behind a flag then.
- No structured error object — a `Type: message` string is enough for a CSV a human reads.

## Runnable check
One assert-based self-check, no network, no framework. Point `request()` at an unroutable/invalid target and assert the returned row is populated with an error and no status:

```python
# tacked into agent-slammer.py under __main__, or a test_request.py
import asyncio

def test_error_row_populated():
    row = asyncio.run(request(target="http://127.0.0.1:1/", agent="pytest-ua", timeout=1.0))
    assert row["error"], "error column must be populated on failure"
    assert row["status code"] == "", "no status on a failed request"
    assert row["agent"] == "pytest-ua"     # row still identifies its UA
    assert "final url" in row and "redirect chain" in row  # keys present for DictWriter

if __name__ == "__main__":
    test_error_row_populated()
    print("ok")
```
Port 1 refuses/times out fast, so this runs offline in ~1s and fails loudly if the try/except or the uniform-keys contract regresses. (Redirect-chain formatting is a one-line `" -> ".join(...)` over `req.history` and needs no dedicated test.)
