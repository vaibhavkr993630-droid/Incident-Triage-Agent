"""query_metrics and list_metrics: read Prometheus.

The model writes PromQL, because no fixed menu of queries could cover every investigation.
Safety does not depend on the query text: the Prometheus query API is read-only, so the worst
a bad query can do is fail or be slow, and both are bounded (length cap, short HTTP timeout,
at most 60 minutes of range, a capped number of series in the answer).

A range query is summarized per series instead of returning raw points: first, last, min,
max, average, a trend word, and 8 evenly spaced samples, which is enough to see when
something changed.
"""

import math

from pydantic import BaseModel, ConfigDict, Field

from triage.tools.base import Tool, ToolError, fit_list

MAX_SERIES = 10
SAMPLES = 8
# Metrics every Python/Prometheus process exports; noise for an incident investigation.
INTERNAL_PREFIXES = ("python_", "process_", "go_", "scrape_", "promhttp_", "prometheus_", "net_")


def num(value: str | float) -> float | None:
    """Prometheus sends numbers as strings; NaN/Inf become None (JSON has no NaN)."""
    x = float(value)
    if math.isnan(x) or math.isinf(x):
        return None
    return float(f"{x:.4g}")


def trend(values: list[float]) -> str:
    """Rising/falling/flat, comparing the first and last thirds of the window."""
    if len(values) < 3:
        return "flat"
    third = max(1, len(values) // 3)
    early = sum(values[:third]) / third
    late = sum(values[-third:]) / third
    scale = max(abs(early), abs(late), 1e-9)
    change = (late - early) / scale
    if change > 0.25:
        return "rising"
    if change < -0.25:
        return "falling"
    return "flat"


def summarize_series(points: list[list]) -> dict:
    values = [v for v in (num(p[1]) for p in points) if v is not None]
    if not values:
        return {"points": len(points), "all_values_missing": True}
    step = max(1, len(values) // SAMPLES)
    return {
        "first": values[0],
        "last": values[-1],
        "min": min(values),
        "max": max(values),
        "avg": num(sum(values) / len(values)),
        "trend": trend(values),
        "samples": values[::step][:SAMPLES],
    }


def format_result(data: dict, ranged: bool) -> dict:
    kind = data["resultType"]
    if kind == "scalar":
        return {"result_type": "scalar", "value": num(data["result"][1])}
    series = data["result"]
    rows = []
    for s in series:
        labels = dict(s["metric"])
        if ranged:
            rows.append({"labels": labels, **summarize_series(s["values"])})
        else:
            rows.append({"labels": labels, "value": num(s["value"][1])})
    # Largest first: in an incident the extreme series are the interesting ones.
    key = "max" if ranged else "value"
    rows.sort(key=lambda r: r.get(key) if r.get(key) is not None else -math.inf, reverse=True)
    out = {"result_type": "range" if ranged else kind, "series_total": len(rows)}
    out["series"] = rows[:MAX_SERIES]
    if not rows:
        out["note"] = "no series matched (check the metric name and labels with list_metrics)"
    return out


class QueryMetricsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(
        min_length=1,
        max_length=600,
        description='PromQL, e.g. sum by (route) (rate(http_requests_total{job="shop-api"}[1m]))',
    )
    minutes: int = Field(
        default=0,
        ge=0,
        le=60,
        description="0 = the value now. N = how the value changed over the last N minutes.",
    )


class QueryMetrics(Tool):
    name = "query_metrics"
    description = (
        "Run a PromQL query against Prometheus, which scrapes shop-api, payment-provider and "
        "worker every 5 s (the job label is the service name). minutes=0 returns current "
        "values; minutes=N summarizes each series over the last N minutes (first, last, min, "
        "max, avg, trend, 8 samples). For latency use histogram_quantile over *_bucket with "
        "rate(...[1m]). Use list_metrics to see which metrics and labels exist."
    )
    Args = QueryMetricsArgs

    def run(self, args: QueryMetricsArgs) -> dict:
        base = self.ctx.endpoints.prometheus
        now = self.ctx.clock()
        end = now.timestamp()
        if args.minutes:
            params = {
                "query": args.query,
                "start": self.ctx.window_start(now, args.minutes).timestamp(),
                "end": end,
                # About 60 points per series, never finer than the 5 s scrape interval.
                "step": max(5, args.minutes),
            }
            body = self.ctx.get_json(f"{base}/api/v1/query_range", params, what="Prometheus")
        else:
            params = {"query": args.query, "time": end}
            body = self.ctx.get_json(f"{base}/api/v1/query", params, what="Prometheus")
        if body.get("status") != "success":
            raise ToolError(f"Prometheus error: {body.get('error', body)}")
        payload = {"query": args.query, **format_result(body["data"], ranged=bool(args.minutes))}
        return fit_list(payload, "series")


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListMetrics(Tool):
    name = "list_metrics"
    description = (
        "List the metrics the lab services export: name, type, help text and label names. "
        "Call this before writing PromQL if you are unsure what exists."
    )
    Args = NoArgs

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._cache: list[dict] | None = None  # the catalog does not change during a run

    def run(self, args: NoArgs) -> dict:
        base = self.ctx.endpoints.prometheus
        if self._cache is None:
            meta = self.ctx.get_json(f"{base}/api/v1/metadata", what="Prometheus")["data"]
            metrics = []
            for name in sorted(meta):
                if name.startswith(INTERNAL_PREFIXES) or name == "up":
                    continue
                info = meta[name][0]
                # Histograms are stored as name_bucket/_sum/_count; labels live on _count.
                series_name = f"{name}_count" if info["type"] == "histogram" else name
                labels = self.ctx.get_json(
                    f"{base}/api/v1/labels", {"match[]": series_name}, what="Prometheus"
                )["data"]
                metrics.append(
                    {
                        "name": name,
                        "type": info["type"],
                        "help": info["help"],
                        "labels": [lbl for lbl in labels if lbl not in ("__name__", "instance")],
                    }
                )
            self._cache = metrics
        return {"metrics": self._cache}
