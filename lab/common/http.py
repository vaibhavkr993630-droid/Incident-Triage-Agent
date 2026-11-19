"""Request logging and metrics middleware shared by the lab's HTTP services."""

import logging
import time
import uuid

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

log = logging.getLogger("lab.http")

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests handled, by route template and status code.",
    ["route", "method", "status"],
)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency, by route template.",
    ["route", "method"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


def route_template(request: Request) -> str:
    """Return '/products/{product_id}' instead of '/products/7'.

    Using the template keeps metric labels and log fields low-cardinality, so queries can
    group by route.
    """
    route = request.scope.get("route")
    return getattr(route, "path", "unmatched")


def install_http_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def observe(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["x-request-id"] = request_id
            return response
        finally:
            elapsed = time.perf_counter() - start
            route = route_template(request)
            # /metrics is scrape noise; /_* is the lab's silent control plane (faults, changes).
            if route != "/metrics" and not route.startswith("/_"):
                HTTP_REQUESTS.labels(route, request.method, str(status)).inc()
                HTTP_DURATION.labels(route, request.method).observe(elapsed)
                level = logging.ERROR if status >= 500 else logging.INFO
                log.log(
                    level,
                    "request handled",
                    extra={
                        "fields": {
                            "request_id": request_id,
                            "method": request.method,
                            "route": route,
                            "status": status,
                            "duration_ms": round(elapsed * 1000, 1),
                        }
                    },
                )

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
