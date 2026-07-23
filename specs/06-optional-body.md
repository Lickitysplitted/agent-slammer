# 06 — Optional response body + response length

## Spec

### Problem
`request()` stores the full `response text` for every user-agent. A single page
can be hundreds of KB (a real run against github.com wrote a ~600KB row), so a CSV
across a large UA list balloons to tens/hundreds of MB — most of it identical HTML.
For the tool's actual job (does UA X land on flow Y), the load-bearing signals are
`status code`, `final url`, and `redirect chain`; the full body is rarely needed and
is expensive to keep.

### Why it matters
Device-flow validation compares outcomes across UAs. The full HTML is noise for that;
worse, it makes the CSV slow to open and diff. But the body isn't useless — its *size*
is a cheap, useful signal (a mobile UA getting a much smaller page is meaningful), and
occasionally you do want the raw HTML to eyeball one case.

### Acceptance criteria
- Default run stores **no** `response text` (empty), but always records `response length`.
- `--body` opt-in flag restores the full `response text` in the CSV.
- `response length` is present on every successful row regardless of the flag.
- Columns stay stable whether or not `--body` is set (both `response length` and
  `response text` columns always exist; text is just blank when the flag is off).
- Failed rows leave both empty (as today).

## Plan

Minimal, threads one bool through the existing chain. No new deps.

1. **`REPORT_FIELDS`**: insert `"response length"` immediately before `"response text"`.
2. **`request()`**: add an `include_body: bool` parameter. Body is already read via
   `await req.text()`; keep reading it (needed for length), then:
   ```python
   text = await req.text()
   reqdata["response length"] = len(text)
   reqdata["response text"] = text if include_body else ""
   ```
   Add `"response length": ""` to the uniform default dict so the key is always present.
3. **`tasker()`**: add `include_body: bool`, pass it into each `request(...)`.
4. **`slam()`**: add `body: bool = typer.Option(False, "--body", help="Store full response text (large)")`
   and thread it: `tasker(url, agent_list, concurrency, timeout, body)`.

### Not doing (YAGNI)
- No truncation-to-N-bytes middle ground — off or full is enough; add a `--max-body`
  later only if someone asks.
- No skipping the body download to save bandwidth. We read it for length anyway; the
  win here is CSV size, not network. If network becomes the concern, switch to
  `req.content_length` (header) and stop reading the body — a separate change.

## Runnable check
The `text if include_body else ""` branch is a trivial one-liner (no test needed per
the project's own rule). What needs verifying is that the flag actually *threads
through* `slam → tasker → request`. Extend the existing concurrency test's fake
`request` to accept `include_body` and assert it receives the value passed to
`tasker()` — a signature/threading regression then fails loudly, offline.
