"""Run every scenario against the live lab: the fault must be visible, then fully undone.

Slow (about a minute per scenario), so it has its own marker and CI job:
    uv run pytest -m scenario
"""

import time

import pytest

from faults.models import Signal, load_scenarios
from faults.runner import Lab, run_scenario

pytestmark = pytest.mark.scenario

SCENARIOS = load_scenarios()
STARTED = time.time()


@pytest.fixture(scope="module")
def lab():
    lab = Lab()
    lab.recover_all()  # start clean even if an earlier run crashed
    yield lab
    lab.recover_all()


@pytest.mark.parametrize("scenario_id", SCENARIOS.keys())
def test_fault_is_visible_and_recovers(lab, scenario_id):
    run = run_scenario(lab, SCENARIOS[scenario_id])
    failed = [
        f"{r.signal.description}: observed {r.observed} {r.signal.op} {r.signal.value}"
        for r in run.signals
        if not r.passed
    ]
    assert not failed, failed
    assert run.recovered, run.baseline_problems_after


def test_control_plane_left_no_trace(lab):
    """Injection must be invisible: no log line or metric mentions the control routes."""
    minutes = int((time.time() - STARTED) / 60) + 2
    for needle in ("/_faults", "/_changes"):
        signal = Signal(
            description=f"no log line mentions {needle}",
            source="loki",
            query=f'sum(count_over_time({{service=~".+"}} |= "{needle}" [{minutes}m]))',
            op="<",
            value=1,
        )
        assert lab.check(signal).passed, needle
    metric = Signal(
        description="no metric series for control routes",
        source="prometheus",
        query='count(http_requests_total{route=~"/_.*"}) or vector(0)',
        op="<",
        value=1,
    )
    assert lab.check(metric).passed
