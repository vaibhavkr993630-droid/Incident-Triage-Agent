"""Apply scenarios to the running lab and check what becomes observable.

Lifecycle of one scenario (``active_fault``):

    1. wait until the lab is at baseline (healthy, no flags set, no scenario changes)
    2. inject
    3. wait ``settle_seconds`` so the fault shows up in metrics and logs
    4. hand control to the caller (the signal check now; the agent and the evals later)
    5. recover, always, even if step 4 raised
    6. wait until the lab is back at baseline

Talking to the lab: control-plane HTTP for fault flags and changes; ``docker compose`` for
container outages and SQL. Metrics and logs are read from Prometheus and Loki, the same
sources the agent will use.
"""

import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from faults.models import ComposeStep, HttpStep, Scenario, Signal, SleepStep, SqlStep, Step

REPO_ROOT = Path(__file__).resolve().parents[1]

SERVICE_URLS = {
    "shop-api": "http://localhost:8000",
    "payment-provider": "http://localhost:8001",
    "worker": "http://localhost:9100",
}
PROMETHEUS_URL = "http://localhost:9090"
LOKI_URL = "http://localhost:3100"
STATS_INDEX = "ix_orders_product_created"

# Baseline: what "healthy" means before a scenario starts and after it recovers.
# Windows are short so recovery is detected quickly.
BASELINE_SIGNALS = [
    Signal(
        description="every lab service is scraped and up",
        source="prometheus",
        query='min(up{job=~"shop-api|payment-provider|worker"})',
        op=">=",
        value=1,
    ),
    Signal(
        description="shop-api serves almost no 5xx",
        source="prometheus",
        query='sum(rate(http_requests_total{job="shop-api",status=~"5.."}[20s]))',
        op="<",
        value=0.05,
    ),
    Signal(
        description="payments are approved",
        source="prometheus",
        query='sum(rate(payment_requests_total{job="shop-api",outcome!="approved"}[20s]))',
        op="<",
        value=0.05,
    ),
    Signal(
        description="the cache is reachable",
        source="prometheus",
        query='sum(rate(cache_requests_total{job="shop-api",result="error"}[20s]))',
        op="<",
        value=0.05,
    ),
    Signal(
        description="the fulfillment queue is drained",
        source="prometheus",
        query='max(worker_queue_depth{job="worker"})',
        op="<",
        value=5,
    ),
    Signal(
        description="the database pool has free connections",
        source="prometheus",
        query='max(db_pool_checked_out{job="shop-api"})',
        op="<",
        value=4,
    ),
    Signal(
        description="product stats are fast (index present)",
        source="prometheus",
        query="histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket"
        '{job="shop-api",route="/products/{product_id}/stats"}[20s])))',
        op="<",
        value=0.05,
    ),
]


class LabNotAtBaseline(RuntimeError):
    pass


@dataclass
class SignalResult:
    signal: Signal
    observed: float | None
    passed: bool


@dataclass
class ScenarioRun:
    scenario_id: str
    signals: list[SignalResult] = field(default_factory=list)
    recovered: bool = False
    baseline_problems_after: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.passed for r in self.signals) and self.recovered


class Lab:
    def __init__(
        self,
        repo_root: Path = REPO_ROOT,
        service_urls: dict[str, str] = SERVICE_URLS,
        prometheus_url: str = PROMETHEUS_URL,
        loki_url: str = LOKI_URL,
        log: Callable[[str], None] = print,
    ) -> None:
        self.repo_root = repo_root
        self.service_urls = service_urls
        self.prometheus_url = prometheus_url
        self.loki_url = loki_url
        self.log = log
        self.http = httpx.Client(timeout=10)

    # ---- steps --------------------------------------------------------------------------

    def apply(self, step: Step) -> None:
        match step:
            case HttpStep():
                url = self.service_urls[step.service] + step.path
                resp = self.http.request(step.method, url, json=step.json_body)
                resp.raise_for_status()
            case ComposeStep():
                self._compose(step.action, step.service)
            case SqlStep():
                self.sql(step.statement)
            case SleepStep():
                time.sleep(step.seconds)

    def _compose(self, *args: str) -> str:
        result = subprocess.run(
            ["docker", "compose", *args],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"docker compose {' '.join(args)} failed: {result.stderr}")
        return result.stdout

    def sql(self, statement: str) -> str:
        return self._compose(
            "exec", "-T", "postgres",
            "psql", "-U", "shop", "-d", "shop", "-v", "ON_ERROR_STOP=1", "-tA", "-c", statement,
        )  # fmt: skip

    # ---- reading telemetry --------------------------------------------------------------

    def query(self, signal: Signal) -> float:
        """Evaluate a signal's query now. An empty result is 0."""
        if signal.source == "prometheus":
            url = f"{self.prometheus_url}/api/v1/query"
        else:
            url = f"{self.loki_url}/loki/api/v1/query"
        resp = self.http.get(url, params={"query": signal.query})
        resp.raise_for_status()
        body = resp.json()
        result = body["data"]["result"]
        if body["data"]["resultType"] == "scalar":
            return float(result[1])
        if not result:
            return 0.0
        if len(result) > 1:
            raise ValueError(f"query returned {len(result)} series, expected 1: {signal.query}")
        value = float(result[0]["value"][1])
        return 0.0 if value != value else value  # NaN (no traffic in window) counts as 0

    def check(self, signal: Signal) -> SignalResult:
        observed = self.query(signal)
        return SignalResult(signal, observed, signal.holds(observed))

    def wait_for_signals(self, signals: list[Signal], timeout: float) -> list[SignalResult]:
        """Poll until every signal holds, or the timeout passes. Returns the last readings."""
        deadline = time.monotonic() + timeout
        while True:
            results = [self.check(s) for s in signals]
            if all(r.passed for r in results) or time.monotonic() >= deadline:
                return results
            time.sleep(3)

    # ---- baseline -----------------------------------------------------------------------

    def baseline_problems(self) -> list[str]:
        problems = []
        try:
            health = self.http.get(self.service_urls["shop-api"] + "/health")
            if health.status_code != 200:
                problems.append(f"shop-api /health returned {health.status_code}")
            for service, url in self.service_urls.items():
                flags = self.http.get(url + "/_faults").json()
                active = {k: v for k, v in flags.items() if v not in (None, False, 0, 0.0, [])}
                if active:
                    problems.append(f"{service} has active fault flags: {active}")
        except httpx.HTTPError as exc:
            problems.append(f"lab service unreachable: {exc}")
            return problems
        if (
            self.sql(f"SELECT count(*) FROM pg_indexes WHERE indexname = '{STATS_INDEX}'").strip()
            != "1"
        ):
            problems.append(f"index {STATS_INDEX} is missing")
        if self.sql("SELECT count(*) FROM changes WHERE source = 'scenario'").strip() != "0":
            problems.append("scenario changes are still in the change log")
        for result in (self.check(s) for s in BASELINE_SIGNALS):
            if not result.passed:
                problems.append(f"{result.signal.description}: observed {result.observed}")
        return problems

    def wait_for_baseline(self, timeout: float = 120) -> list[str]:
        """Return [] once the lab is at baseline, else the remaining problems at timeout."""
        deadline = time.monotonic() + timeout
        while True:
            problems = self.baseline_problems()
            if not problems or time.monotonic() >= deadline:
                return problems
            time.sleep(3)

    def recover_all(self) -> None:
        """Undo every possible fault, whatever state a crashed run left behind."""
        self._compose("start", "redis")
        for url in self.service_urls.values():
            try:
                self.http.delete(url + "/_faults").raise_for_status()
            except httpx.HTTPError as exc:
                self.log(f"could not clear faults at {url}: {exc}")
        self.sql(f"CREATE INDEX IF NOT EXISTS {STATS_INDEX} ON orders (product_id, created_at)")
        self.sql("DELETE FROM changes WHERE source = 'scenario'")


@contextmanager
def active_fault(lab: Lab, scenario: Scenario, baseline_timeout: float = 120) -> Iterator[None]:
    problems = lab.wait_for_baseline(baseline_timeout)
    if problems:
        raise LabNotAtBaseline(f"lab not at baseline before {scenario.id}: {problems}")
    lab.log(f"[{scenario.id}] " + ("injecting" if scenario.inject else "control: nothing injected"))
    try:
        for step in scenario.inject:
            lab.apply(step)
        if scenario.settle_seconds:
            lab.log(f"[{scenario.id}] settling for {scenario.settle_seconds}s")
            time.sleep(scenario.settle_seconds)
        yield
    finally:
        lab.log(f"[{scenario.id}] recovering")
        for step in scenario.recover:
            lab.apply(step)


def run_scenario(lab: Lab, scenario: Scenario) -> ScenarioRun:
    """Inject, check that every signal becomes visible, recover, confirm baseline."""
    run = ScenarioRun(scenario.id)
    with active_fault(lab, scenario):
        run.signals = lab.wait_for_signals(scenario.signals, scenario.signal_timeout_seconds)
        for r in run.signals:
            mark = "ok  " if r.passed else "FAIL"
            lab.log(f"  {mark} {r.signal.description}: {r.observed} {r.signal.op} {r.signal.value}")
    run.baseline_problems_after = lab.wait_for_baseline()
    run.recovered = not run.baseline_problems_after
    lab.log(f"[{scenario.id}] {'recovered' if run.recovered else 'NOT recovered'}")
    return run
