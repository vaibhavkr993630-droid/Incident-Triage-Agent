"""Scenario schema. Each file in ``faults/scenarios/`` is one ``Scenario``.

A scenario says how to break the lab (``inject``), how to undo it (``recover``), what the true
cause is (``ground_truth``), what the on-call engineer is told (``alert``), and which
observable signals prove the fault is really visible (``signals``).
"""

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCENARIO_DIR = Path(__file__).parent / "scenarios"


class Category(StrEnum):
    """Root-cause categories. The agent's report must pick one of these."""

    DB_SLOW_QUERY = "db_slow_query"
    DB_POOL_EXHAUSTED = "db_pool_exhausted"
    CACHE_UNAVAILABLE = "cache_unavailable"
    BAD_CONFIG_CHANGE = "bad_config_change"
    BAD_RELEASE = "bad_release"
    DEPENDENCY_SLOW = "dependency_slow"
    DEPENDENCY_ERRORS = "dependency_errors"
    WORKER_BACKLOG = "worker_backlog"
    NO_FAULT = "no_fault"


class Component(StrEnum):
    SHOP_API = "shop-api"
    PAYMENT_PROVIDER = "payment-provider"
    WORKER = "worker"
    POSTGRES = "postgres"
    REDIS = "redis"
    NONE = "none"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HttpStep(_Strict):
    """Call a service's control plane, e.g. set fault flags or record a change."""

    type: Literal["http"]
    service: Literal["shop-api", "payment-provider", "worker"]
    method: Literal["PUT", "POST", "DELETE"]
    path: str = Field(pattern=r"^/_")
    json_body: dict | None = Field(default=None, alias="json")


class ComposeStep(_Strict):
    """Stop or start a container, like an infrastructure outage."""

    type: Literal["compose"]
    action: Literal["stop", "start"]
    service: Literal["redis", "postgres", "payment-provider", "loki"]


class SqlStep(_Strict):
    """Run SQL as the database owner, like an operator changing the schema."""

    type: Literal["sql"]
    statement: str


class SleepStep(_Strict):
    type: Literal["sleep"]
    seconds: float = Field(gt=0, le=120)


Step = Annotated[HttpStep | ComposeStep | SqlStep | SleepStep, Field(discriminator="type")]


class Signal(_Strict):
    """One observable fact that must hold while the fault is active.

    The query must return a single number (an instant vector with at most one sample).
    An empty result counts as 0.
    """

    description: str
    source: Literal["prometheus", "loki"]
    query: str
    op: Literal[">", ">=", "<", "<="]
    value: float

    def holds(self, observed: float) -> bool:
        return {
            ">": observed > self.value,
            ">=": observed >= self.value,
            "<": observed < self.value,
            "<=": observed <= self.value,
        }[self.op]


class GroundTruth(_Strict):
    category: Category
    component: Component
    # Facts a correct investigation should surface; used to score evidence in the evals.
    key_evidence: list[str] = Field(min_length=1)


class Attack(_Strict):
    """A prompt-injection attempt in the scenario: what the attacker wants the agent to say."""

    description: str
    target_category: Category
    target_component: Component
    # Text that appears only in the planted payload: a run "saw" the attack if any tool
    # result contains it. A run that never saw it cannot be credited with resisting it.
    marker: str


class Scenario(_Strict):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    title: str
    # What the on-call engineer (the agent) is told. Symptoms only, never the cause.
    alert: str
    ground_truth: GroundTruth
    inject: list[Step] = []
    recover: list[Step] = []
    signals: list[Signal] = Field(min_length=1)
    attack: Attack | None = None
    settle_seconds: int = Field(default=25, ge=0, le=300)
    signal_timeout_seconds: int = Field(default=60, ge=5, le=300)

    @model_validator(mode="after")
    def _consistent(self) -> "Scenario":
        is_control = self.ground_truth.category is Category.NO_FAULT
        if is_control and (self.inject or self.recover):
            raise ValueError("a no_fault control scenario must not inject anything")
        if not is_control and not self.inject:
            raise ValueError("a fault scenario must inject something")
        if self.inject and not self.recover:
            raise ValueError("a scenario that injects must also recover")
        if is_control != (self.ground_truth.component is Component.NONE):
            raise ValueError("component 'none' is used exactly for no_fault scenarios")
        return self


def load_scenario(path: Path) -> Scenario:
    scenario = Scenario.model_validate(yaml.safe_load(path.read_text()))
    if scenario.id != path.stem:
        raise ValueError(f"{path.name}: id '{scenario.id}' must match the file name")
    return scenario


def load_scenarios(directory: Path = SCENARIO_DIR) -> dict[str, Scenario]:
    scenarios = [load_scenario(p) for p in sorted(directory.glob("*.yml"))]
    return {s.id: s for s in scenarios}
