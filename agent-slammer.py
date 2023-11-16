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


async def tasker(target: str, agents: json) -> List[dict]:
    if target and agents:
        reqtasks = []
        reqlog = []
        for agent in track(agents, description="Building task list"):
            task = asyncio.create_task(request(target=target, agent=agent["ua"]))
            reqtasks.append(task)
        for reqtask in track(reqtasks, description="Performing queries"):
            reqlog.append(await reqtask)
        return reqlog
    pass

async def request(target: str, agent: str) -> dict:
    if target and agent:
        log = logging.getLogger("charset_normalizer")
        log.setLevel(logging.WARNING)
        async with aiohttp.ClientSession() as session:
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
        logger.warning("CHECK-FAIL: Missing target URL and/or user agents")


def reporter(reppath: Path, repdata: List[dict]) -> None:
    if reppath and repdata:
        with open(reppath.resolve(), "a", encoding="utf-8") as repobj:
            writer = DictWriter(
                repobj,
                [
                    "agent",
                    "url",
                    "status code",
                    "request headers",
                    "response headers",
                    "cookies",
                    "response text",
                ],
            )
            writer.writeheader()
            for entry in repdata:
                writer.writerow(
                    {
                        "agent": entry.get("agent"),
                        "url": entry.get("url"),
                        "status code": entry.get("status code"),
                        "request headers": entry.get("request headers"),
                        "response headers": entry.get("response headers"),
                        "cookies": entry.get("cookies"),
                        "response text": entry.get("response text"),
                    }
                )
    else:
        logger.warning("CHECK-FAIL: Missing report path and/or report data")


@app.command()
def slam(
    url: str = typer.Option(help="Target URL"),
    report: Path = typer.Option(help="Report output path"),
    agents: Path = typer.Option(
        default=Path("user-agents.json"),
        help="JSON file containing the user agent strings"
        ),
    verbose: int = typer.Option(
        2, "-v", count=True, max=4, help="Log verbosity level"
    ),
) -> None:
    if url and report:
        logging.basicConfig(level=(verbose * 10) - 40)
        f = open(agents)
        agents = json.load(f)
        report = Path(report)
        reporter(
            reppath=report, repdata=(asyncio.run(tasker(target=url, agents=agents)))
        )
    else:
        logger.warning("CHECK-FAIL: Missing target URL and/or report path")


if __name__ == "__main__":
    app()
