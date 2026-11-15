"""Lab settings, read from environment variables (Docker Compose sets them)."""

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    payment_url: str
    # Small on purpose: a pool of 5 with no overflow makes pool exhaustion observable.
    db_pool_size: int
    db_pool_timeout: float
    payment_timeout: float
    cache_ttl_seconds: int
    log_level: str


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql+asyncpg://shop:shop@localhost:5432/shop"
        ),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        payment_url=os.environ.get("PAYMENT_URL", "http://localhost:8001"),
        db_pool_size=_int("DB_POOL_SIZE", 5),
        db_pool_timeout=_float("DB_POOL_TIMEOUT", 2.0),
        payment_timeout=_float("PAYMENT_TIMEOUT", 2.0),
        cache_ttl_seconds=_int("CACHE_TTL_SECONDS", 30),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
