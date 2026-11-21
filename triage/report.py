"""The agent's final answer: a structured, checkable root-cause report.

The model returns it by calling the ``submit_report`` tool, so the provider's function-calling
support does the JSON shaping, and pydantic does the validation. A report that fails
validation, or cites a tool call that does not exist, is sent back to the model to fix.

The category and component lists mirror the fault scenarios' vocabulary on purpose (a unit
test keeps them in sync), but this module never imports the ``faults`` package: the agent must
not have access to the answer key.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Category(StrEnum):
    DB_SLOW_QUERY = "db_slow_query"
    DB_POOL_EXHAUSTED = "db_pool_exhausted"
    CACHE_UNAVAILABLE = "cache_unavailable"
    BAD_CONFIG_CHANGE = "bad_config_change"
    BAD_RELEASE = "bad_release"
    DEPENDENCY_SLOW = "dependency_slow"
    DEPENDENCY_ERRORS = "dependency_errors"
    WORKER_BACKLOG = "worker_backlog"
    NO_FAULT = "no_fault"
    # Not a cause: the honest answer when the evidence does not support a conclusion.
    INCONCLUSIVE = "inconclusive"


CATEGORY_HELP = {
    Category.DB_SLOW_QUERY: "a database query became slow (e.g. missing index)",
    Category.DB_POOL_EXHAUSTED: "the app's database connection pool has no free connections",
    Category.CACHE_UNAVAILABLE: "the cache / queue store is down or unreachable",
    Category.BAD_CONFIG_CHANGE: "a recent configuration change caused the problem",
    Category.BAD_RELEASE: "a recent code release introduced a bug",
    Category.DEPENDENCY_SLOW: "an external dependency became slow",
    Category.DEPENDENCY_ERRORS: "an external dependency is returning errors",
    Category.WORKER_BACKLOG: "background jobs are processed too slowly and pile up",
    Category.NO_FAULT: "nothing is actually wrong; telemetry is healthy",
    Category.INCONCLUSIVE: "the evidence is not enough to name a cause",
}


class Component(StrEnum):
    SHOP_API = "shop-api"
    PAYMENT_PROVIDER = "payment-provider"
    WORKER = "worker"
    POSTGRES = "postgres"
    REDIS = "redis"
    NONE = "none"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_call_id: str = Field(pattern=r"^t\d+$", description="Id of the tool call, e.g. t3.")
    finding: str = Field(
        min_length=5, max_length=300, description="What that tool result shows, specifically."
    )


class RuledOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hypothesis: str = Field(min_length=3, max_length=150)
    reason: str = Field(min_length=5, max_length=300)


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Category = Field(description="Root-cause category.")
    component: Component = Field(
        description="The component where the root cause is, not where symptoms appear. "
        "'none' only for no_fault or inconclusive."
    )
    summary: str = Field(
        min_length=20, max_length=700, description="Root cause and how it causes the symptoms."
    )
    evidence: list[Evidence] = Field(
        min_length=1, max_length=8, description="Facts from tool results, each citing its id."
    )
    ruled_out: list[RuledOut] = Field(
        default=[], max_length=5, description="Plausible causes you checked and rejected."
    )
    confidence: float = Field(ge=0, le=1, description="0 to 1.")
    suggested_next_step: str = Field(
        min_length=5,
        max_length=300,
        description="What an engineer should do next. You must not do it yourself.",
    )


class ReportOutcome(StrEnum):
    SUBMITTED = "submitted"  # the model submitted a valid report
    FORCED = "forced"  # a budget ran out; the model submitted when told to stop
    FAILED = "failed"  # no valid report; an inconclusive placeholder was produced


def placeholder_report(reason: str) -> Report:
    """Used when the agent could not produce a valid report. Honest, never a guess."""
    return Report(
        category=Category.INCONCLUSIVE,
        component=Component.NONE,
        summary=f"No conclusion was reached: {reason}"[:700].ljust(20),
        evidence=[Evidence(tool_call_id="t0", finding="no valid report was produced")],
        confidence=0,
        suggested_next_step="Investigate manually; the automated triage did not finish.",
    )
