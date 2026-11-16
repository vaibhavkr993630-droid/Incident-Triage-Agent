"""Integration tests: Prometheus and Loki hold the lab's metrics and logs.

These are the two data sources the triage agent's tools will query, so the tests use the same
kinds of queries the tools will use (PromQL rates by job, LogQL with `| json` filters).
"""

import json
import time

import httpx
import pytest

pytestmark = pytest.mark.integration

PROMETHEUS = "http://localhost:9090"
LOKI = "http://localhost:3100"
LAB_JOBS = {"shop-api", "payment-provider", "worker"}
LOG_SERVICES = {"shop-api", "payment-provider", "worker", "loadgen"}


def promql(query: str) -> list[dict]:
    resp = httpx.get(f"{PROMETHEUS}/api/v1/query", params={"query": query}, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    assert body["status"] == "success", body
    return body["data"]["result"]


def logql(query: str, minutes: int = 5, limit: int = 200) -> list[dict]:
    """Return entries as dicts: the stream labels plus the parsed JSON line."""
    now = time.time_ns()
    resp = httpx.get(
        f"{LOKI}/loki/api/v1/query_range",
        params={
            "query": query,
            "start": now - minutes * 60 * 10**9,
            "end": now,
            "limit": limit,
            "direction": "backward",
        },
        timeout=10,
    )
    resp.raise_for_status()
    entries = []
    for stream in resp.json()["data"]["result"]:
        for _ts, line in stream["values"]:
            entries.append({"labels": stream["stream"], "line": json.loads(line)})
    return entries


def wait_until(check, timeout: float = 30, interval: float = 1):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = check()
        if last:
            return last
        time.sleep(interval)
    return last


def test_prometheus_scrapes_every_lab_service():
    def all_up():
        resp = httpx.get(f"{PROMETHEUS}/api/v1/targets", timeout=10).json()
        targets = resp["data"]["activeTargets"]
        healthy = {t["labels"]["job"] for t in targets if t["health"] == "up"}
        return healthy == LAB_JOBS

    assert wait_until(all_up), "not every lab service is scraped and up"


def test_request_rate_is_queryable_per_route():
    result = wait_until(
        lambda: promql('sum by (route) (rate(http_requests_total{job="shop-api"}[1m]))')
    )
    rates = {r["metric"]["route"]: float(r["value"][1]) for r in result}
    assert rates.get("/products/{product_id}", 0) > 0
    assert rates.get("/orders", 0) > 0


def test_latency_percentile_is_queryable():
    result = wait_until(
        lambda: promql(
            "histogram_quantile(0.95, sum by (le) "
            '(rate(http_request_duration_seconds_bucket{job="shop-api"}[1m])))'
        )
    )
    p95 = float(result[0]["value"][1])
    assert 0 < p95 < 1, f"healthy p95 should be well under a second, got {p95}"


def test_worker_and_pool_metrics_are_present():
    assert wait_until(lambda: promql('worker_jobs_total{job="worker",outcome="fulfilled"}'))
    pool = promql('db_pool_size{job="shop-api"}')
    assert float(pool[0]["value"][1]) == 5


def test_loki_has_logs_from_every_service():
    def services_seen():
        seen = set()
        for service in LOG_SERVICES:
            if logql(f'{{service="{service}"}}', limit=1):
                seen.add(service)
        return seen == LOG_SERVICES

    assert wait_until(services_seen), "some service's logs did not reach Loki"


def test_log_fields_are_filterable_with_logql_json():
    entries = wait_until(
        lambda: logql('{service="shop-api"} | json | route="/orders" | status="201"', limit=20)
    )
    assert entries
    for entry in entries:
        assert entry["labels"]["service"] == "shop-api"
        assert entry["line"]["route"] == "/orders"
        assert entry["line"]["status"] == 201


def test_no_log_lines_are_being_dropped():
    for job in LAB_JOBS:
        dropped = promql(f'sum(log_shipping_dropped_total{{job="{job}"}})')
        value = float(dropped[0]["value"][1]) if dropped else 0.0
        assert value == 0, f"{job} dropped {value} log lines"
