"""Fulfillment worker: takes jobs off the Redis queue and marks orders fulfilled.

It also runs a periodic restock so the steady load never drains the inventory (an empty
inventory would look like a fault when none was injected).
"""

import asyncio
import json
import logging
import time

import redis.asyncio as redis
from prometheus_client import Counter, Gauge, Histogram, start_http_server
from sqlalchemy import text

from lab.common.db import init_schema, make_engine, wait_for_database
from lab.common.log_setup import setup_logging
from lab.common.settings import load_settings

SERVICE = "worker"
FULFILLMENT_QUEUE = "fulfillment"
METRICS_PORT = 9100
RESTOCK_EVERY_S = 30
RESTOCK_BELOW = 200
RESTOCK_TO = 1000

settings = load_settings()
setup_logging(SERVICE, settings.log_level)
log = logging.getLogger("worker")

JOBS = Counter("worker_jobs_total", "Fulfillment jobs by outcome.", ["outcome"])
JOB_DURATION = Histogram(
    "worker_job_duration_seconds",
    "Time to fulfil one order.",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
QUEUE_DEPTH = Gauge("worker_queue_depth", "Jobs waiting in the fulfillment queue.")
REDIS_ERRORS = Counter("worker_redis_errors_total", "Redis errors seen by the worker.")
RESTOCKED = Counter("worker_restocked_products_total", "Products restocked.")


async def fulfil(engine, order_id: int) -> None:
    start = time.perf_counter()
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "UPDATE orders SET status = 'fulfilled', fulfilled_at = now() "
                "WHERE id = :id AND status = 'paid'"
            ),
            {"id": order_id},
        )
    JOB_DURATION.observe(time.perf_counter() - start)
    if result.rowcount == 1:
        JOBS.labels("fulfilled").inc()
        log.info("order fulfilled", extra={"fields": {"order_id": order_id}})
    else:
        JOBS.labels("skipped").inc()
        log.warning("order not in paid state, skipped", extra={"fields": {"order_id": order_id}})


async def consume(engine, client: redis.Redis) -> None:
    while True:
        try:
            item = await client.brpop([FULFILLMENT_QUEUE], timeout=1)
            QUEUE_DEPTH.set(await client.llen(FULFILLMENT_QUEUE))
        except redis.RedisError as exc:
            REDIS_ERRORS.inc()
            log.error(
                "cannot read fulfillment queue", extra={"fields": {"error": type(exc).__name__}}
            )
            await asyncio.sleep(1)
            continue
        if item is None:
            continue
        try:
            order_id = json.loads(item[1])["order_id"]
            await fulfil(engine, order_id)
        except Exception:
            JOBS.labels("failed").inc()
            log.exception("fulfillment job failed", extra={"fields": {"raw": item[1].decode()}})


async def restock(engine) -> None:
    while True:
        await asyncio.sleep(RESTOCK_EVERY_S)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(
                    text("UPDATE products SET stock = :to WHERE stock < :below"),
                    {"to": RESTOCK_TO, "below": RESTOCK_BELOW},
                )
            if result.rowcount:
                RESTOCKED.inc(result.rowcount)
                log.info("restocked products", extra={"fields": {"count": result.rowcount}})
        except Exception:
            log.exception("restock failed")


async def main() -> None:
    start_http_server(METRICS_PORT)
    engine = make_engine(settings)
    await wait_for_database(engine)
    await init_schema(engine)
    client = redis.from_url(settings.redis_url, socket_timeout=3, socket_connect_timeout=1)
    log.info("worker started", extra={"fields": {"queue": FULFILLMENT_QUEUE}})
    await asyncio.gather(consume(engine, client), restock(engine))


if __name__ == "__main__":
    asyncio.run(main())
