"""The agent loop: the model decides, our code acts, until a valid report is submitted.

    messages = [system prompt, alert]
    repeat (bounded by turns, tool calls, tokens and wall-clock time):
        reply = model.chat(messages, tools)
        if the reply calls tools:   run each one (read-only), append "[t3] <result>"
        if it calls submit_report:  validate; accept, or send the errors back to fix
        if it is just prose:        remind it to call a tool or submit
    when a budget runs out:         require submit_report on the next turn
    if no valid report:             an honest "inconclusive" placeholder, never a guess

Every turn, tool call and token count is recorded in the AgentRun, which is saved as a trace.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import ValidationError

from triage import prompts
from triage.llm import ChatModel, LLMError, Usage
from triage.report import Category, Component, Report, ReportOutcome, placeholder_report
from triage.tools.base import ToolCall, ToolRegistry, ToolSession, parameters_schema, utc_now

SUBMIT = "submit_report"
MAX_INVALID_REPORTS = 3


@dataclass(frozen=True)
class Limits:
    max_turns: int = 15
    max_tool_calls: int = 20
    max_seconds: float = 300
    max_tokens: int = 250_000


@dataclass
class Turn:
    number: int
    provider: str
    model: str
    latency_ms: int
    attempts: int
    usage: Usage
    finish_reason: str
    text: str
    requested: list[str]  # tool names the model asked for, in order


@dataclass
class AgentRun:
    alert: str
    started_at: datetime
    limits: Limits
    turns: list[Turn] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    report: Report | None = None
    outcome: ReportOutcome = ReportOutcome.FAILED
    stop_reason: str = ""
    invalid_reports: list[str] = field(default_factory=list)
    error: str | None = None
    finished_at: datetime | None = None

    @property
    def usage(self) -> Usage:
        return Usage(
            sum(t.usage.prompt_tokens for t in self.turns),
            sum(t.usage.completion_tokens for t in self.turns),
        )

    @property
    def seconds(self) -> float:
        end = self.finished_at or utc_now()
        return round((end - self.started_at).total_seconds(), 1)


def submit_tool_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": SUBMIT,
            "description": "Submit the final root-cause report. Ends the investigation.",
            "parameters": parameters_schema(Report),
        },
    }


def check_report(raw_arguments: str, session: ToolSession) -> tuple[Report | None, str | None]:
    """Validate a submitted report. Returns (report, None) or (None, error for the model)."""
    try:
        data = json.loads(raw_arguments) if raw_arguments.strip() else {}
        report = Report.model_validate(data)
    except json.JSONDecodeError as exc:
        return None, f"submit_report arguments are not valid JSON: {exc.msg}"
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'report'}: {e['msg']}" for e in exc.errors()
        )
        return None, f"report rejected: {problems}"

    problems = []
    for item in report.evidence:
        call = session.get(item.tool_call_id)
        if call is None:
            problems.append(f"evidence cites {item.tool_call_id}, but no such tool call exists")
        elif call.is_error:
            problems.append(f"evidence cites {item.tool_call_id}, which returned an error")
    no_cause = report.category in (Category.NO_FAULT, Category.INCONCLUSIVE)
    if no_cause != (report.component is Component.NONE):
        problems.append(
            "component must be 'none' exactly when category is no_fault or inconclusive"
        )
    if problems:
        return None, "report rejected: " + "; ".join(problems)
    return report, None


class TriageAgent:
    def __init__(self, model: ChatModel, registry: ToolRegistry, limits: Limits = Limits()):
        self.model = model
        self.registry = registry
        self.limits = limits

    def _over_budget(self, run: AgentRun, session: ToolSession, start: float) -> str | None:
        if len(session.calls) >= self.limits.max_tool_calls:
            return f"{self.limits.max_tool_calls} tool calls"
        if run.usage.total >= self.limits.max_tokens:
            return f"{self.limits.max_tokens} tokens"
        if time.monotonic() - start >= self.limits.max_seconds:
            return f"{self.limits.max_seconds:.0f} seconds"
        return None

    def investigate(self, alert: str) -> AgentRun:
        start = time.monotonic()
        run = AgentRun(alert=alert, started_at=utc_now(), limits=self.limits)
        session = ToolSession(self.registry, max_calls=self.limits.max_tool_calls)
        tools = self.registry.schemas() + [submit_tool_schema()]
        messages: list[dict] = [
            {"role": "system", "content": prompts.system_prompt(self.limits.max_tool_calls)},
            {
                "role": "user",
                "content": prompts.alert_message(
                    alert, run.started_at.isoformat(timespec="seconds")
                ),
            },
        ]
        forced = False

        for number in range(1, self.limits.max_turns + 1):
            reason = self._over_budget(run, session, start)
            if reason is None and number == self.limits.max_turns:
                reason = f"{self.limits.max_turns} turns"
            tool_choice: str | dict = "auto"
            if reason:
                if not forced:
                    messages.append({"role": "user", "content": prompts.budget_message(reason)})
                    forced = True
                tool_choice = {"type": "function", "function": {"name": SUBMIT}}

            try:
                reply = self.model.chat(messages, tools, tool_choice)
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
            messages.append(reply.as_message())

            if not reply.tool_calls:
                cut_off = reply.finish_reason == "length"
                reminder = prompts.CUT_OFF_REMINDER if cut_off else prompts.NO_TOOL_CALL_REMINDER
                messages.append({"role": "user", "content": reminder})
                continue

            # Run investigation tools first, so a report in the same turn can cite them.
            ordered = sorted(reply.tool_calls, key=lambda c: c.name == SUBMIT)
            for call in ordered:
                if call.name != SUBMIT:
                    record = session.call(call.name, call.arguments)
                    content = f"[{record.id}] {record.result}"
                else:
                    report, problem = check_report(call.arguments, session)
                    if report is not None:
                        run.report = report
                        content = "Report accepted."
                    else:
                        run.invalid_reports.append(problem)
                        content = problem + ". Fix it and call submit_report again."
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content})

            if run.report is not None:
                run.outcome = ReportOutcome.FORCED if forced else ReportOutcome.SUBMITTED
                run.stop_reason = "report submitted"
                break
            if len(run.invalid_reports) >= MAX_INVALID_REPORTS:
                run.stop_reason = f"{MAX_INVALID_REPORTS} invalid reports"
                break
        else:
            run.stop_reason = run.stop_reason or "turn limit"

        run.tool_calls = list(session.calls)
        if run.report is None:
            run.outcome = ReportOutcome.FAILED
            run.report = placeholder_report(run.error or run.stop_reason or "no report submitted")
        run.finished_at = utc_now()
        return run
