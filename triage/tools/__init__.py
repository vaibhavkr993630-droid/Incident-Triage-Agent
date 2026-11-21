"""The triage agent's read-only investigation tools."""

from triage.tools.base import ToolCall, ToolContext, ToolRegistry, ToolSession
from triage.tools.changes import ListRecentChanges
from triage.tools.health import ServiceHealth
from triage.tools.logs import QueryLogs
from triage.tools.metrics import ListMetrics, QueryMetrics
from triage.tools.runbooks import SearchRunbooks

TOOL_CLASSES = [
    ServiceHealth,
    QueryLogs,
    QueryMetrics,
    ListMetrics,
    ListRecentChanges,
    SearchRunbooks,
]


def build_registry(ctx: ToolContext | None = None) -> ToolRegistry:
    ctx = ctx or ToolContext()
    return ToolRegistry([cls(ctx) for cls in TOOL_CLASSES])


__all__ = ["ToolCall", "ToolContext", "ToolRegistry", "ToolSession", "build_registry"]
