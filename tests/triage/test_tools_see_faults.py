"""Can the tools see each fault? Inject it, investigate with the tools only, check the evidence.

This proves the scenarios are solvable *through the agent's tools*. If a tool hid the key
evidence (bad grouping, a cap that drops it, a missing field), the evals would later blame
the model for a tool problem. The probes are what a competent on-call engineer would run;
they use only tool output, never the scenario's ground truth.

Slow (about a minute per scenario): marker `scenario`, run with
    uv run pytest -m scenario tests/triage
"""

import json
from collections.abc import Callable

import pytest

from faults.models import load_scenarios
from faults.runner import Lab, active_fault
from triage.tools import ToolContext, ToolSession, build_registry

pytestmark = pytest.mark.scenario

SCENARIOS = load_scenarios()


class Investigation:
    def __init__(self, not_before) -> None:
        ctx = ToolContext(not_before=not_before)
        self.session = ToolSession(build_registry(ctx), max_calls=50)

    def call(self, name: str, **args) -> dict:
        record = self.session.call(name, args)
        assert not record.is_error, record.result
        return json.loads(record.result)

    def log_groups(self, **args) -> list[dict]:
        return self.call("query_logs", minutes=2, **args)["groups"]

    def group(self, msg_part: str, **args) -> dict | None:
        return next((g for g in self.log_groups(**args) if msg_part in g["msg"]), None)

    def metric(self, query: str) -> float:
        series = self.call("query_metrics", query=query)["series"]
        return series[0]["value"] if series and series[0]["value"] is not None else 0.0

    def health(self) -> dict[str, dict]:
        return {s["service"]: s for s in self.call("service_health")["services"]}

    def recent_changes(self, minutes: int = 15) -> list[dict]:
        return self.call("list_recent_changes", minutes=minutes)["changes"]


def p95(job: str, route: str) -> str:
    return (
        "histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket"
        f'{{job="{job}",route="{route}"}}[1m])))'
    )


def probe_missing_index(inv: Investigation) -> None:
    slow = inv.group("slow database query", service="shop-api", level="warning")
    assert slow and slow["fields"]["query"]["values"].get("product_stats", 0) >= 3
    assert inv.metric(p95("shop-api", "/products/{product_id}/stats")) > 0.05
    assert inv.log_groups(service="shop-api", level="error") == []


def probe_connection_leak(inv: Investigation) -> None:
    assert inv.group("database connection pool exhausted", level="error")["count"] >= 3
    assert inv.metric('max(db_pool_checked_out{job="shop-api"})') == 5
    assert inv.health()["shop-api"]["http_status"] == 503


def probe_redis_down(inv: Investigation) -> None:
    checks = inv.health()["shop-api"]["body"]["checks"]
    assert checks["redis"].startswith("error") and checks["postgres"] == "ok"
    assert inv.group("cache read failed", service="shop-api", level="warning")["count"] >= 5
    assert inv.group("cannot read fulfillment queue", service="worker", level="error")


def probe_bad_payment_timeout_config(inv: Investigation) -> None:
    change = next(c for c in inv.recent_changes() if "PAYMENT_TIMEOUT" in c["summary"])
    assert change["service"] == "shop-api" and change["minutes_ago"] < 5
    timeouts = inv.group("payment provider timed out", level="error")
    assert timeouts["fields"]["timeout_s"]["values"] == {"0.03": timeouts["count"]}
    assert inv.metric(p95("payment-provider", "/charge")) < 0.15  # the provider is fine


def probe_payment_slow(inv: Investigation) -> None:
    timeouts = inv.group("payment provider timed out", level="error")
    assert timeouts["fields"]["timeout_s"]["values"] == {"2.0": timeouts["count"]}
    assert inv.metric(p95("payment-provider", "/charge")) > 1
    assert inv.recent_changes() == []  # nothing changed recently on our side


def probe_payment_errors(inv: Investigation) -> None:
    assert inv.group("card processor unavailable", service="payment-provider", level="error")
    shop = inv.group("payment provider returned an error", service="shop-api")
    assert shop["fields"]["provider_status"]["values"] == {"500": shop["count"]}


def probe_worker_backlog(inv: Investigation) -> None:
    assert inv.metric('max(worker_queue_depth{job="worker"})') > 15
    assert inv.group("fulfillment job slow", service="worker", level="warning")
    assert inv.metric('sum(rate(orders_total{job="shop-api",outcome="created"}[1m]))') > 0.5


def probe_bad_release(inv: Investigation) -> None:
    release = next(c for c in inv.recent_changes() if "v1.8.0" in c["summary"])
    assert release["kind"] == "deploy" and release["minutes_ago"] < 5
    crash = inv.group("Exception in ASGI application", service="shop-api", level="error")
    assert crash["exception"]["errors"][0] == "KeyError: 'discount_cents'"
    assert crash["exception"]["app_frame"].startswith("lab/shop_api/app.py:")


def probe_payment_slow_with_decoy(inv: Investigation) -> None:
    # The decoy is loud at warning level ...
    assert inv.group("deprecated header", service="shop-api", level="warning")["count"] >= 20
    # ... but an error-level search still leads straight to the real problem.
    errors = inv.log_groups(level="error")
    assert any("payment provider timed out" in g["msg"] for g in errors)
    assert inv.metric(p95("payment-provider", "/charge")) > 1


def probe_no_fault(inv: Investigation) -> None:
    assert inv.log_groups(level="error") == []
    assert all(s["http_status"] == 200 for s in inv.health().values())
    assert inv.recent_changes() == []


PROBES: dict[str, Callable[[Investigation], None]] = {
    name.removeprefix("probe_"): fn for name, fn in globals().items() if name.startswith("probe_")
}


def test_every_scenario_has_a_probe():
    assert set(PROBES) == set(SCENARIOS)


@pytest.fixture(scope="module")
def lab():
    lab = Lab()
    lab.recover_all()
    yield lab
    lab.recover_all()


@pytest.mark.parametrize("scenario_id", sorted(SCENARIOS))
def test_tools_surface_the_key_evidence(lab, scenario_id):
    with active_fault(lab, SCENARIOS[scenario_id]) as window:
        inv = Investigation(not_before=window.healthy_since)
        PROBES[scenario_id](inv)
    oversized = [c.id for c in inv.session.calls if c.truncated]
    assert not oversized, f"tool results hit the hard size cap: {oversized}"
