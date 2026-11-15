"""Database engine, schema and seed data shared by the shop API and the worker."""

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from lab.common.settings import Settings

log = logging.getLogger(__name__)

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
    "CREATE INDEX IF NOT EXISTS ix_orders_product_id ON orders (product_id)",
    "CREATE INDEX IF NOT EXISTS ix_orders_status ON orders (status)",
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
    """Create tables if missing and seed products once.

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
