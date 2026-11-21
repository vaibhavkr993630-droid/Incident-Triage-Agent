"""The baseline with scripted model replies and local stand-in tools (no network)."""

import json

from pydantic import BaseModel, ConfigDict

from evals.baseline import SNAPSHOT, run_baseline
from triage.llm import ChatResult, LLMError, ToolCallRequest, Usage
from triage.report import ReportOutcome
from triage.tools.base import Tool, ToolContext, ToolRegistry


class AnyArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


def stand_in(tool_name):
    class StandIn(Tool):
        name = tool_name
        description = "stand-in"
        Args = AnyArgs

        def run(self, args):
            return {"tool": tool_name}

    return StandIn


REGISTRY = ToolRegistry([stand_in(n)(ToolContext()) for n in {n for n, _ in SNAPSHOT}])
REPORT = json.dumps({
    "category": "dependency_slow", "component": "payment-provider",
    "summary": "The payment provider became slow, so checkout times out.",
    "evidence": [{"tool_call_id": "t2", "finding": "timeouts in error logs"}],
    "confidence": 0.7, "suggested_next_step": "Escalate to the provider.",
})  # fmt: skip


class Scripted:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def chat(self, messages, tools, tool_choice="auto"):
        self.calls.append((messages, tools, tool_choice))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def reply(args: str) -> ChatResult:
    return ChatResult("", [ToolCallRequest("c1", "submit_report", args)], "stop", Usage(900, 50),
                      "p", "m", 5)  # fmt: skip


def test_one_call_with_the_whole_snapshot_and_a_forced_report():
    model = Scripted(reply(REPORT))
    run = run_baseline(model, REGISTRY, "Checkout errors")
    assert run.outcome is ReportOutcome.SUBMITTED and len(run.turns) == 1
    assert len(run.tool_calls) == len(SNAPSHOT) == 12
    messages, tools, choice = model.calls[0]
    assert [t["function"]["name"] for t in tools] == ["submit_report"]
    assert choice == {"type": "function", "function": {"name": "submit_report"}}
    assert "[t12]" in messages[1]["content"] and "search_runbooks" not in messages[0]["content"]


def test_an_invalid_report_gets_one_retry():
    model = Scripted(reply('{"category": "nope"}'), reply(REPORT))
    run = run_baseline(model, REGISTRY, "x")
    assert run.outcome is ReportOutcome.SUBMITTED and len(run.turns) == 2
    assert len(run.invalid_reports) == 1


def test_model_errors_give_an_honest_failure():
    run = run_baseline(Scripted(LLMError("quota", 429)), REGISTRY, "x")
    assert run.outcome is ReportOutcome.FAILED and run.error == "quota"
    assert run.report.category.value == "inconclusive"
