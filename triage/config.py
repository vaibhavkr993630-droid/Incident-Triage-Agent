"""Where the agent's tools find the lab's telemetry. Defaults match docker-compose.yml."""

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).rstrip("/")


@dataclass(frozen=True)
class Endpoints:
    prometheus: str = field(
        default_factory=lambda: _env("TRIAGE_PROMETHEUS_URL", "http://localhost:9090")
    )
    loki: str = field(default_factory=lambda: _env("TRIAGE_LOKI_URL", "http://localhost:3100"))
    # Only GET /health is ever called on these, plus GET /_changes on shop-api (the change log).
    services: dict[str, str] = field(
        default_factory=lambda: {
            "shop-api": _env("TRIAGE_SHOP_URL", "http://localhost:8000"),
            "payment-provider": _env("TRIAGE_PAYMENT_URL", "http://localhost:8001"),
            "worker": _env("TRIAGE_WORKER_URL", "http://localhost:9100"),
        }
    )
