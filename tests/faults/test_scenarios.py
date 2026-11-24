"""Static checks on the scenario files: consistent, complete, and no answer leaks."""

import logging

import pytest

from faults.models import Category, HttpStep, Scenario, SqlStep, load_scenarios

SCENARIOS = load_scenarios()

# Words that name a cause. An alert describes symptoms only; if it contained one of these,
# the agent could "diagnose" by reading the alert.
CAUSE_WORDS = [
    "index", "leak", "pool", "redis", "cache", "timeout", "release", "deploy", "config",
    "backlog", "provider", "payment", "discount", "decoy", "postgres", "database", "worker",
]  # fmt: skip


@pytest.fixture(scope="module")
def fault_models():
    """The real flag models, imported from the services (importing them sets up logging)."""
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    from lab.payment_provider.app import ProviderFaults
    from lab.shop_api.app import ShopFaults
    from lab.worker.main import WorkerFaults

    root.handlers[:], level = saved
    root.setLevel(level)
    return {"shop-api": ShopFaults, "payment-provider": ProviderFaults, "worker": WorkerFaults}


def test_attacks_aim_away_from_the_true_cause():
    attacked = [s for s in SCENARIOS.values() if s.attack]
    assert attacked
    for s in attacked:
        assert s.attack.target_category != s.ground_truth.category


def test_there_are_twelve_scenarios_covering_every_category():
    assert len(SCENARIOS) == 12
    used = {s.ground_truth.category for s in SCENARIOS.values()}
    assert used == set(Category)


@pytest.mark.parametrize("scenario", SCENARIOS.values(), ids=SCENARIOS.keys())
def test_alert_describes_symptoms_without_naming_the_cause(scenario: Scenario):
    alert = scenario.alert.lower()
    assert scenario.ground_truth.category.value not in alert
    leaked = [w for w in CAUSE_WORDS if w in alert]
    assert not leaked, f"alert names a cause: {leaked}"


@pytest.mark.parametrize("scenario", SCENARIOS.values(), ids=SCENARIOS.keys())
def test_fault_flags_match_the_service_models(scenario: Scenario, fault_models):
    for step in scenario.inject:
        if isinstance(step, HttpStep) and step.path == "/_faults" and step.method == "PUT":
            fault_models[step.service].model_validate(step.json_body)


@pytest.mark.parametrize("scenario", SCENARIOS.values(), ids=SCENARIOS.keys())
def test_every_flag_service_is_cleared_on_recover(scenario: Scenario):
    flagged = {
        s.service for s in scenario.inject if isinstance(s, HttpStep) and s.path == "/_faults"
    }
    cleared = {
        s.service
        for s in scenario.recover
        if isinstance(s, HttpStep) and s.path == "/_faults" and s.method == "DELETE"
    }
    assert flagged <= cleared


@pytest.mark.parametrize("scenario", SCENARIOS.values(), ids=SCENARIOS.keys())
def test_recorded_changes_are_removed_on_recover(scenario: Scenario):
    records_change = any(isinstance(s, HttpStep) and s.path == "/_changes" for s in scenario.inject)
    removes_changes = any(
        isinstance(s, SqlStep) and "DELETE FROM changes WHERE source = 'scenario'" in s.statement
        for s in scenario.recover
    )
    assert records_change == removes_changes


def test_shared_checkout_alert_forces_investigation():
    """Five different causes produce the same checkout alert; the alert alone cannot decide."""
    checkout = [s for s in SCENARIOS.values() if s.alert.startswith("Checkout errors")]
    assert len({s.ground_truth.category for s in checkout}) >= 4


def test_invalid_scenarios_are_rejected():
    base = SCENARIOS["payment_slow"].model_dump(by_alias=True)
    with pytest.raises(ValueError, match="must also recover"):
        Scenario.model_validate({**base, "recover": []})
    with pytest.raises(ValueError, match="must not inject"):
        Scenario.model_validate(
            {**base, "ground_truth": {**base["ground_truth"], "category": "no_fault",
                                      "component": "none"}}
        )  # fmt: skip
    with pytest.raises(ValueError):
        Scenario.model_validate({**base, "unknown_field": 1})
