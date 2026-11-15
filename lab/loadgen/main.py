"""Load generator: steady, realistic traffic against the shop API.

Without traffic there is nothing to observe: a slow query or a dead cache only shows up in
logs and metrics when requests hit it. The mix is mostly reads with some orders.
"""

import asyncio
import logging
import os
import random
import time
from collections import Counter

import httpx

from lab.common.log_setup import setup_logging

SERVICE = "loadgen"
SHOP_URL = os.environ.get("SHOP_URL", "http://localhost:8000")
RPS = float(os.environ.get("LOADGEN_RPS", "5"))
REPORT_EVERY_S = 15
PRODUCT_IDS = range(1, 11)

setup_logging(SERVICE, os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("loadgen")


def pick_request() -> tuple[str, str, dict | None]:
    roll = random.random()
    if roll < 0.5:
        return "GET", f"/products/{random.choice(PRODUCT_IDS)}", None
    if roll < 0.7:
        return "GET", "/products", None
    return (
        "POST",
        "/orders",
        {"product_id": random.choice(PRODUCT_IDS), "quantity": random.randint(1, 3)},
    )


async def fire(client: httpx.AsyncClient, stats: Counter) -> None:
    method, path, body = pick_request()
    try:
        resp = await client.request(method, path, json=body)
        stats[str(resp.status_code)] += 1
    except httpx.HTTPError as exc:
        stats[type(exc).__name__] += 1


async def main() -> None:
    log.info("loadgen started", extra={"fields": {"target": SHOP_URL, "rps": RPS}})
    stats: Counter = Counter()
    last_report = time.monotonic()
    tasks: set[asyncio.Task] = set()
    async with httpx.AsyncClient(base_url=SHOP_URL, timeout=10) as client:
        while True:
            # Fire-and-forget at a fixed rate, so a slow API does not slow the arrival rate
            # (real users keep arriving even when the service is struggling).
            task = asyncio.create_task(fire(client, stats))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            await asyncio.sleep(random.expovariate(RPS))
            if time.monotonic() - last_report >= REPORT_EVERY_S:
                log.info(
                    "traffic summary",
                    extra={
                        "fields": {
                            "window_s": REPORT_EVERY_S,
                            "results": dict(stats),
                            "in_flight": len(tasks),
                        }
                    },
                )
                stats.clear()
                last_report = time.monotonic()


if __name__ == "__main__":
    asyncio.run(main())
