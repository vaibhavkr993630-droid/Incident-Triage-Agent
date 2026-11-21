"""The non-agentic baseline: one fixed telemetry snapshot, one model call, one report.

It answers the question an interviewer should ask about any agent: does the investigation
loop beat simply dumping a good dashboard into the prompt?

The snapshot is deliberately strong: the same queries an experienced engineer would put on
a checkout dashboard (health, error and warning logs, recent changes, error rates, latency
by route, the provider's own latency, pool usage, queue depth, payment and cache outcomes,
query latency). It is collected through the same tools as the agent, so every item has an
id the report can cite, and the same report validation applies. What the baseline cannot do
is follow up: no second query, no narrowing down.
"""

from triage.agent import (
    SUBMIT,
    AgentRun,
    Limits,
    Turn,
    check_report,
    submit_tool_schema,
)
from triage.llm import ChatModel, LLMError
from triage.prompts import alert_message, system_prompt
from triage.report import ReportOutcome, placeholder_report
from triage.tools.base import ToolRegistry, ToolSession, utc_now

SHOP = 'job="shop-api"'
P95 = "histogram_quantile(0.95, sum by ({by}) (rate({metric}_bucket{{{sel}}}[2m])))"
METRIC_QUERIES = [
    f'sum by (route, status) (rate(http_requests_total{{{SHOP},status=~"5.."}}[2m]))',
    P95.format(by="le, route", metric="http_request_duration_seconds", sel=SHOP),
    P95.format(
        by="le",
        metric="http_request_duration_seconds",
        sel='job="payment-provider",route="/charge"',
    ),
    f"sum by (outcome) (rate(payment_requests_total{{{SHOP}}}[2m]))",
    f"sum by (result) (rate(cache_requests_total{{{SHOP}}}[2m]))",
    P95.format(by="le, query", metric="db_query_duration_seconds", sel=SHOP),
    f"max(db_pool_checked_out{{{SHOP}}})",
    'max(worker_queue_depth{job="worker"})',
]
SNAPSHOT: list[tuple[str, dict]] = [
    ("service_health", {}),
    ("query_logs", {"level": "error", "minutes": 10}),
    ("query_logs", {"level": "warning", "minutes": 10}),
    ("list_recent_changes", {"minutes": 1440}),
    *[("query_metrics", {"query": q}) for q in METRIC_QUERIES],
]

INSTRUCTION = (
    "You cannot run tools in this mode. Below is a telemetry snapshot collected when the "
    "alert fired; each item starts with its id, like [t3]. Work out the root cause from it "
    "and call submit_report, citing those ids."
)


def snapshot_text(session: ToolSession) -> str:
    return "\n\n".join(f"[{c.id}] {c.name} {c.arguments}\n{c.result}" for c in session.calls)


def run_baseline(model: ChatModel, registry: ToolRegistry, alert: str) -> AgentRun:
    limits = Limits(max_turns=2, max_tool_calls=len(SNAPSHOT))
    run = AgentRun(alert=alert, started_at=utc_now(), limits=limits)
    session = ToolSession(registry, max_calls=len(SNAPSHOT))
    for name, args in SNAPSHOT:
        session.call(name, args)

    messages = [
        {"role": "system", "content": system_prompt(len(SNAPSHOT), runbooks=False)},
        {
            "role": "user",
            "content": alert_message(alert, run.started_at.isoformat(timespec="seconds"))
            + f"\n\n{INSTRUCTION}\n\n{snapshot_text(session)}",
        },
    ]
    force = {"type": "function", "function": {"name": SUBMIT}}
    for number in (1, 2):  # one attempt, plus one retry if the report is invalid
        try:
            reply = model.chat(messages, [submit_tool_schema()], force)
        except LLMError as exc:
            run.error, run.stop_reason = str(exc), "model error"
            break
        run.turns.append(
            Turn(
                number,
                reply.provider,
                reply.model,
                reply.latency_ms,
                reply.attempts,
                reply.usage,
                reply.finish_reason,
                reply.content[:2000],
                [c.name for c in reply.tool_calls],
            )  # fmt: skip
        )
        call = next((c for c in reply.tool_calls if c.name == SUBMIT), None)
        if call is None:
            problem = "no submit_report call"
        else:
            report, problem = check_report(call.arguments, session)
            if report is not None:
                run.report, run.outcome, run.stop_reason = (
                    report,
                    ReportOutcome.SUBMITTED,
                    "report submitted",
                )
                break
        run.invalid_reports.append(problem)
        messages.append(reply.as_message())
        for c in reply.tool_calls:
            messages.append(
                {"role": "tool", "tool_call_id": c.id, "content": f"{problem}. Fix it."}
            )
        if not reply.tool_calls:
            messages.append({"role": "user", "content": f"{problem}. Call submit_report."})
    else:
        run.stop_reason = "invalid reports"

    run.tool_calls = list(session.calls)
    if run.report is None:
        run.outcome = ReportOutcome.FAILED
        run.report = placeholder_report(run.error or run.stop_reason or "no report")
    run.finished_at = utc_now()
    return run
