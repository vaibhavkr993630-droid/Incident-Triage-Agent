"""Database engine, schema and seed data shared by the shop API and the worker."""

import asyncio
import logging
import os
import time
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from lab.common.settings import Settings

log = logging.getLogger(__name__)

# The product-stats query filters orders by product and time window. With this index it takes
# a few milliseconds; without it, Postgres scans every order. The "missing index" fault drops
# exactly this index.
STATS_INDEX_NAME = "ix_orders_product_created"
STATUS_INDEX_SQL = "CREATE INDEX IF NOT EXISTS ix_orders_status ON orders (status)"
STATS_INDEX_SQL = (
    f"CREATE INDEX IF NOT EXISTS {STATS_INDEX_NAME} ON orders (product_id, created_at)"
)

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS products (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        price_cents INTEGER NOT NULL CHECK (price_cents > 0),
        stock INTEGER NOT NULL CHECK (stock >= 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        id SERIAL PRIMARY KEY,
        product_id INTEGER NOT NULL REFERENCES products(id),
        quantity INTEGER NOT NULL CHECK (quantity > 0),
        amount_cents INTEGER NOT NULL,
        status TEXT NOT NULL,
        charge_id TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        fulfilled_at TIMESTAMPTZ
    )
    """,
    # Deploy and config-change history: what a real team would see in its deploy tool.
    """
    CREATE TABLE IF NOT EXISTS changes (
        id SERIAL PRIMARY KEY,
        ts TIMESTAMPTZ NOT NULL DEFAULT now(),
        service TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('deploy', 'config')),
        summary TEXT NOT NULL,
        details JSONB NOT NULL DEFAULT '{}'::jsonb,
        -- Hidden from the change listing. 'scenario' rows were written by the fault injector
        -- and are deleted on recovery, so one eval scenario never leaks into the next.
        source TEXT NOT NULL DEFAULT 'history' CHECK (source IN ('history', 'scenario'))
    )
    """,
    "CREATE TABLE IF NOT EXISTS lab_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    # Replaced by the composite stats index (kept here so old local volumes converge).
    "DROP INDEX IF EXISTS ix_orders_product_id",
]

SEED_PRODUCTS = [
    ("Mechanical keyboard", 4999),
    ("Wireless mouse", 1999),
    ("27-inch monitor", 18999),
    ("USB-C hub", 2999),
    ("Laptop stand", 2499),
    ("Noise-cancelling headphones", 12999),
    ("Webcam", 5999),
    ("Desk lamp", 1599),
    ("External SSD 1TB", 8999),
    ("Ergonomic chair", 24999),
]
INITIAL_STOCK = 1000

# An established shop has history. 2M past orders make a missing index measurably slow
# (about 2 ms with the index, 300-800 ms without, measured on the dev laptop).
HISTORY_ORDERS = int(os.environ.get("HISTORY_ORDERS", "2000000"))

# Benign past changes, so the change log is never empty and never a giveaway by itself.
# Each matches the lab's real settings (cache TTL 30 s, restock every 30 s).
SEED_CHANGES = [
    (
        timedelta(days=3),
        "payment-provider",
        "deploy",
        "Release payment-provider v2.3.0: update receipt wording",
    ),
    (timedelta(hours=26), "shop-api", "config", "Raise CACHE_TTL_SECONDS from 15 to 30"),
    (timedelta(hours=5), "worker", "deploy", "Release worker v1.4.1: restock interval 60s to 30s"),
]


def make_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=0,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,
    )


async def wait_for_database(engine: AsyncEngine, attempts: int = 30) -> None:
    for attempt in range(1, attempts + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001 - any connection error means "not ready yet"
            log.warning(
                "database not ready",
                extra={"fields": {"attempt": attempt, "error": type(exc).__name__}},
            )
            await asyncio.sleep(1)
    raise RuntimeError("database did not become ready")


async def init_schema(engine: AsyncEngine) -> None:
    """Create tables if missing and seed products, history and past changes once.

    The lab uses plain DDL instead of migrations: it is a disposable test system that is
    reset between eval runs, not an application whose data must survive schema changes.
    An advisory lock stops the API and the worker from seeding at the same moment.
    """
    async with engine.begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(42)"))
        for statement in SCHEMA:
            await conn.execute(text(statement))

        count = (await conn.execute(text("SELECT count(*) FROM products"))).scalar_one()
        if count == 0:
            await conn.execute(
                text("INSERT INTO products (name, price_cents, stock) VALUES (:n, :p, :s)"),
                [{"n": n, "p": p, "s": INITIAL_STOCK} for n, p in SEED_PRODUCTS],
            )
            log.info("seeded products", extra={"fields": {"count": len(SEED_PRODUCTS)}})

        seeded = (
            await conn.execute(text("SELECT value FROM lab_meta WHERE key = 'history_seeded'"))
        ).scalar_one_or_none()
        if seeded is None:
            start = time.perf_counter()
            # Skip per-row foreign-key checks for this one bulk load: the generated
            # product ids are always 1-10, which were seeded above. Without this the load
            # takes ~95 s instead of ~15 s. SET LOCAL ends with this transaction.
            await conn.execute(text("SET LOCAL session_replication_role = replica"))
            # History is spread over the last 90 days; only a sliver falls inside the
            # stats query's 24-hour window, which is why the index matters so much.
            await conn.execute(
                text(
                    "INSERT INTO orders "
                    "(product_id, quantity, amount_cents, status, charge_id, created_at, "
                    " fulfilled_at) "
                    "SELECT 1 + (g % 10), 1 + (g % 3), 1000 + (g % 20000), 'fulfilled', "
                    "       'ch_history', ts, ts + interval '2 minutes' "
                    "FROM (SELECT g, now() - random() * interval '90 days' AS ts "
                    "      FROM generate_series(1, :n) g) h"
                ),
                {"n": HISTORY_ORDERS},
            )
            await conn.execute(
                text("INSERT INTO lab_meta VALUES ('history_seeded', :n)"),
                {"n": str(HISTORY_ORDERS)},
            )
            log.info(
                "seeded order history",
                extra={
                    "fields": {
                        "rows": HISTORY_ORDERS,
                        "duration_ms": round((time.perf_counter() - start) * 1000),
                    }
                },
            )

        # Re-dated on every start, so the benign history is always "a few hours/days ago"
        # relative to now, however long ago the lab volume was created.
        await conn.execute(text("DELETE FROM changes WHERE source = 'history'"))
        for age, service, kind, summary in SEED_CHANGES:
            await conn.execute(
                text(
                    "INSERT INTO changes (ts, service, kind, summary) "
                    "VALUES (now() - CAST(:age AS interval), :service, :kind, :summary)"
                ),
                {"age": age, "service": service, "kind": kind, "summary": summary},
            )

        # Indexes are built after the bulk load (faster than maintaining them row by row).
        await conn.execute(text("SET LOCAL session_replication_role = origin"))
        await conn.execute(text(STATUS_INDEX_SQL))
        await conn.execute(text(STATS_INDEX_SQL))
        await conn.execute(text("ANALYZE orders"))
