import asyncio
import logging
import json
from csv import DictWriter
from pathlib import Path
from typing import List

import aiohttp
import typer
from rich.progress import track

__author__ = "Lickitysplitted"
__version__ = "0.0.3"

app = typer.Typer()
logger = logging.getLogger(__name__)

# Single source of truth for CSV columns — must stay in sync with the keys
# request() emits. Add a column here and in request() together.
REPORT_FIELDS = [
    "agent",
    "url",
    "status code",
    "final url",
    "redirect chain",
    "response headers",
    "cookies",
    "response length",
    "response text",
    "error",
]


async def tasker(
    target: str,
    agents: List[dict],
    concurrency: int,
    timeout: float,
    include_body: bool,
) -> List[dict]:
    if not (target and agents):
        return []
    sem = asyncio.Semaphore(concurrency)
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
        tasks = [
            asyncio.create_task(request(session, sem, target, agent["ua"], include_body))
            for agent in agents
        ]
        reqlog = []
        for task in track(tasks, description="Performing queries"):
            reqlog.append(await task)
    return reqlog


async def request(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    target: str,
    agent: str,
    include_body: bool,
) -> dict:
    # All keys present up front so every row is uniform for the CSV writer.
    reqdata = {
        "agent": agent,
        "url": target,
        "status code": "",
        "final url": "",
        "redirect chain": "",
        "response headers": "",
        "cookies": "",
        "response length": "",
        "response text": "",
        "error": "",
    }
    try:
        async with sem:  # ponytail: guards the GET, not task creation — create_task starts work immediately
            async with session.get(target, headers={"user-agent": agent}) as req:
                text = await req.text()
                reqdata.update(
                    {
                        "status code": req.status,
                        "final url": str(req.url),
                        "redirect chain": " -> ".join(str(h.url) for h in req.history),
                        "response headers": req.headers,
                        "cookies": req.cookies,
                        "response length": len(text),
                        "response text": text if include_body else "",
                    }
                )
    except Exception as exc:  # ponytail: broad on purpose — a validation run must never lose a row
        reqdata["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("REQUEST-FAIL [%s]: %s", agent, exc)
    return reqdata


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


@app.command()
def slam(
    url: str = typer.Option(help="Target URL"),
    report: Path = typer.Option(help="Report output path"),
    agents: Path = typer.Option(
        default=Path("user-agents.json"),
        help="JSON file containing the user agent strings"
        ),
    concurrency: int = typer.Option(
        20, help="Max requests in flight at once"
    ),
    timeout: float = typer.Option(
        10.0, help="Per-request timeout in seconds"
    ),
    body: bool = typer.Option(
        False, "--body", help="Store full response text (large)"
    ),
    verbose: int = typer.Option(
        0, "-v", count=True, max=4, help="Log verbosity level"
    ),
) -> None:
    if not (url and report):
        logger.critical("CHECK-FAIL: Missing target URL and/or report path")
        return
    logging.basicConfig(level=max(10, 50 - verbose * 10))
    with open(agents) as f:
        agent_list = json.load(f)
    reporter(
        reppath=Path(report),
        repdata=asyncio.run(tasker(url, agent_list, concurrency, timeout, body)),
    )


if __name__ == "__main__":
    app()
