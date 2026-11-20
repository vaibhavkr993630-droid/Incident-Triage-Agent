"""The tools against the live, healthy lab (`docker compose up`), plus the read-only proof."""

import json
import re
import time

import httpx
import pytest

from triage.tools import ToolContext, ToolSession, build_registry

pytestmark = pytest.mark.integration

# Everything the tools are allowed to touch: read-only query APIs, health checks, change log.
ALLOWED_PATHS = re.compile(
    r"^(/api/v1/(query|query_range|metadata|labels)|/loki/api/v1/query_range|/health|/_changes)$"
)


class RecordingTransport(httpx.BaseTransport):
    """A real HTTP transport that also records every request it sends."""

    def __init__(self) -> None:
        self.inner = httpx.HTTPTransport()
        self.requests: list[tuple[str, str]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        return self.inner.handle_request(request)


@pytest.fixture
def recorded():
    transport = RecordingTransport()
    ctx = ToolContext(http=httpx.Client(transport=transport, timeout=10))
    return ToolSession(build_registry(ctx)), transport


def result(call) -> dict:
    assert not call.is_error, call.result
    return json.loads(call.result)


def test_service_health_reports_every_service_healthy(recorded):
    session, _ = recorded
    services = {s["service"]: s for s in result(session.call("service_health"))["services"]}
    assert set(services) == {"shop-api", "payment-provider", "worker"}
    for s in services.values():
        assert s["scrape_up"] is True and s["http_status"] == 200
    assert services["shop-api"]["body"]["checks"] == {"postgres": "ok", "redis": "ok"}


def test_recent_changes_show_the_benign_history(recorded):
    session, _ = recorded
    changes = result(session.call("list_recent_changes", {"minutes": 4400}))["changes"]
    assert [c["service"] for c in changes] == ["worker", "shop-api", "payment-provider"]
    assert all(c["minutes_ago"] > 60 for c in changes)
    assert "source" not in changes[0]  # the hidden injector column is never exposed


def test_query_logs_groups_healthy_traffic():
    # Start the window at a confirmed-healthy moment, so startup noise from a freshly started
    # stack (CI runs this right after `docker compose up`) cannot fail the "no errors" check.
    from datetime import UTC, datetime

    from faults.runner import Lab

    assert Lab().wait_for_baseline() == []
    ctx = ToolContext(not_before=datetime.now(UTC))
    time.sleep(8)
    session = ToolSession(build_registry(ctx))
    out = result(session.call("query_logs", {"service": "shop-api", "minutes": 2}))
    handled = next(g for g in out["groups"] if g["msg"] == "request handled")
    assert handled["count"] > 10
    assert "/orders" in handled["fields"]["route"]["values"]
    assert result(session.call("query_logs", {"level": "error", "minutes": 2}))["groups"] == []


def test_query_metrics_instant_and_range(recorded):
    session, _ = recorded
    q = 'sum by (route) (rate(http_requests_total{job="shop-api"}[1m]))'
    now = result(session.call("query_metrics", {"query": q}))
    assert now["series_total"] >= 4 and now["series"][0]["value"] > 0
    over = result(session.call("query_metrics", {"query": q, "minutes": 5}))
    assert over["result_type"] == "range"
    assert {"first", "last", "min", "max", "trend", "samples"} <= over["series"][0].keys()


def test_bad_promql_comes_back_as_an_error_the_model_can_fix(recorded):
    session, _ = recorded
    call = session.call("query_metrics", {"query": "rate(http_requests_total[1m"})
    assert call.is_error
    assert "Prometheus" in json.loads(call.result)["error"]


def test_list_metrics_describes_the_lab_metrics(recorded):
    session, _ = recorded
    metrics = {m["name"]: m for m in result(session.call("list_metrics"))["metrics"]}
    assert metrics["http_request_duration_seconds"]["type"] == "histogram"
    assert {"job", "route", "method"} <= set(metrics["http_request_duration_seconds"]["labels"])
    assert "db_pool_checked_out" in metrics and "worker_queue_depth" in metrics
    assert not any(name.startswith(("python_", "process_")) for name in metrics)


def test_tools_are_read_only_and_never_touch_fault_controls(recorded):
    session, transport = recorded
    session.call("service_health")
    session.call("list_recent_changes")
    session.call("query_logs", {"level": "warning"})
    session.call("query_metrics", {"query": "up"})
    session.call("query_metrics", {"query": "up", "minutes": 3})
    session.call("list_metrics")
    assert transport.requests
    for method, path in transport.requests:
        assert method == "GET", (method, path)
        assert ALLOWED_PATHS.match(path), path
    assert "/_faults" not in json.dumps([c.result for c in session.calls])
