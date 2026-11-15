"""Integration tests against the running lab stack (`docker compose up -d --build --wait`)."""

import json
import re
import subprocess
import time

import httpx
import pytest

pytestmark = pytest.mark.integration

SHOP = "http://localhost:8000"
PAYMENT = "http://localhost:8001"
WORKER_METRICS = "http://localhost:9100/metrics"


def metric_total(text: str, name: str, **labels: str) -> float:
    """Sum every sample of `name` whose labels include `labels`."""
    total = 0.0
    for line in text.splitlines():
        if not line.startswith(name + "{") and not line.startswith(name + " "):
            continue
        if all(f'{k}="{v}"' in line for k, v in labels.items()):
            total += float(line.rsplit(" ", 1)[1])
    return total


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=SHOP, timeout=10) as c:
        yield c


def test_health_reports_postgres_and_redis_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "checks": {"postgres": "ok", "redis": "ok"}}


def test_seeded_products_are_listed(client):
    products = client.get("/products").json()
    assert len(products) == 10
    assert {"id", "name", "price_cents", "stock"} <= products[0].keys()


def test_unknown_product_and_invalid_order_are_rejected(client):
    assert client.get("/products/9999").status_code == 404
    assert client.post("/orders", json={"product_id": 9999, "quantity": 1}).status_code == 404
    assert client.post("/orders", json={"product_id": 1, "quantity": 0}).status_code == 422


def test_order_is_paid_then_fulfilled_by_worker(client):
    resp = client.post("/orders", json={"product_id": 2, "quantity": 1})
    assert resp.status_code == 201, resp.text
    order = resp.json()
    assert order["status"] == "paid"
    assert order["amount_cents"] == 1999

    deadline = time.monotonic() + 10
    status = None
    while time.monotonic() < deadline:
        status = client.get(f"/orders/{order['id']}").json()["status"]
        if status == "fulfilled":
            break
        time.sleep(0.25)
    assert status == "fulfilled"


def test_every_service_exposes_its_metrics(client):
    shop = client.get("/metrics").text
    for name in [
        "http_requests_total",
        "http_request_duration_seconds",
        "db_query_duration_seconds",
        "db_pool_checked_out",
        "cache_requests_total",
        "payment_requests_total",
        "orders_total",
    ]:
        assert name in shop, name
    assert "http_requests_total" in httpx.get(f"{PAYMENT}/metrics").text
    worker = httpx.get(WORKER_METRICS).text
    assert "worker_jobs_total" in worker
    assert metric_total(worker, "worker_jobs_total", outcome="fulfilled") > 0


def test_route_labels_use_templates_not_raw_ids(client):
    client.get("/products/3")
    shop = client.get("/metrics").text
    assert 'route="/products/{product_id}"' in shop
    assert not re.search(r'route="/products/\d+"', shop)


def test_loadgen_produces_steady_traffic(client):
    before = metric_total(client.get("/metrics").text, "http_requests_total")
    time.sleep(4)
    after = metric_total(client.get("/metrics").text, "http_requests_total")
    # 5 requests/second by default; allow wide slack for a slow laptop.
    assert after - before >= 8


def test_cache_serves_repeated_reads(client):
    shop = client.get("/metrics").text
    assert metric_total(shop, "cache_requests_total", result="hit") > 0
    assert metric_total(shop, "cache_requests_total", result="error") == 0


@pytest.mark.parametrize("service", ["shop-api", "payment-provider", "worker", "loadgen"])
def test_logs_are_json_with_contract_fields(service):
    out = subprocess.run(
        ["docker", "compose", "logs", service, "--no-log-prefix", "--tail", "40"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines, f"no logs from {service}"
    for line in lines:
        entry = json.loads(line)
        assert {"ts", "level", "service", "logger", "msg"} <= entry.keys()
        assert entry["service"] == service
