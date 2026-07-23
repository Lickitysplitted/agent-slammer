# 04 — Reporter: duplicate header row + phantom column

## Spec

`reporter()` writes per-request dicts to a CSV. Two defects corrupt the output.

### Defect 1 — unconditional header row in append mode
The file is opened `"a"` (append) but `writer.writeheader()` runs every call. Point
the tool at the same report path twice and a second header row lands in the middle
of the data. The CSV becomes unparseable by anything that trusts the first row as the
schema — a header line reappears as a fake data row.

### Defect 2 — phantom `request headers` column
`fieldnames` lists `"request headers"`, but no row dict ever carries that key. Every
row leaves that column blank, so the CSV advertises a column that is always empty —
misleading, and a trap for downstream code that assumes columns map to data.

### Acceptance criteria
- Running `reporter()` twice against the same file yields **exactly one** header row.
- Every column in `fieldnames` has a corresponding key in the row dicts (no always-blank columns).
- No blank interstitial lines between rows (Windows CSV newline handling).
- Fieldname/row drift never raises — extra keys on a row are silently dropped, not fatal.

## Plan

Changes to `reporter()` in `agent-slammer.py`:

1. **Header only when new/empty.** Guard `writeheader()` with
   `if not reppath.exists() or reppath.stat().st_size == 0:`. Compute this *before*
   opening in append mode (opening `"a"` does not change size, but read it first for clarity).
2. **Drop `"request headers"`** from the fieldnames.
3. **Open with `newline=""`** — the csv module's documented requirement; prevents blank
   lines between rows on Windows.
4. **`extrasaction="ignore"`** on `DictWriter` — if a row later gains keys the fieldnames
   don't list, they're skipped instead of raising.

### Single source of truth for columns
Multiple specs touch the CSV columns (`final url`, `redirect chain`, `error` are being
added elsewhere). Promote the fieldnames to a module-level constant so every spec edits
one list and the row dicts and writer stay in sync:

```python
REPORT_FIELDS = [
    "agent",
    "url",
    "status code",
    "response headers",
    "cookies",
    "response text",
]
```

> Sync note: whoever adds `final url` / `redirect chain` / `error` appends them here **and**
> to the per-request dict. Fieldnames and row keys must move together — that is the whole
> point of the constant. `extrasaction="ignore"` covers a row having *fewer* keys, not the
> writer silently swallowing a *new* one you forgot to declare.

### Rewritten function

```python
def reporter(reppath: Path, repdata: List[dict]) -> None:
    if not (reppath and repdata):
        logger.critical("CHECK-FAIL: Missing report path and/or report data")
        return
    reppath = reppath.resolve()
    write_header = not reppath.exists() or reppath.stat().st_size == 0
    with open(reppath, "a", encoding="utf-8", newline="") as repobj:
        writer = DictWriter(repobj, REPORT_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(repdata)
```

`writerows` replaces the manual loop — stdlib already does it.

## Check

One `assert`-based self-check, no network, no framework. Call `reporter()` twice against
a temp file and assert the header appears exactly once.

```python
def _test_reporter_single_header():
    import tempfile, os
    rows = [{"agent": "a", "url": "u", "status code": "200",
             "response headers": "{}", "cookies": "", "response text": "hi"}]
    fd, name = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    p = Path(name)
    try:
        reporter(p, rows)
        reporter(p, rows)  # reuse same path
        text = p.read_text(encoding="utf-8")
        header = ",".join(REPORT_FIELDS)
        assert text.count(header) == 1, f"expected 1 header, got {text.count(header)}"
        assert "request headers" not in text, "phantom column leaked"
    finally:
        p.unlink()

# run: python -c "import agent_slammer as m; m._test_reporter_single_header()"
```
