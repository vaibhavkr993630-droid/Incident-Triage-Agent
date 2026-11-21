"""The agent loop with a scripted model and local tools: every branch, no network."""

import json

from pydantic import BaseModel, ConfigDict

from triage import prompts
from triage.agent import Limits, TriageAgent, submit_tool_schema
from triage.llm import ChatResult, LLMError, ToolCallRequest, Usage
from triage.report import ReportOutcome
from triage.tools.base import Tool, ToolContext, ToolRegistry
from triage.trace import save_run


class LookArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str


class Look(Tool):
    name = "look"
    description = "Look at something."
    Args = LookArgs

    def run(self, args):
        return {"saw": args.target}


def report(*ids: str, category="dependency_slow", component="payment-provider") -> str:
    return json.dumps(
        {
            "category": category,
            "component": component,
            "summary": "The payment provider became slow, so checkout calls time out.",
            "evidence": [{"tool_call_id": i, "finding": f"result {i} shows it"} for i in ids],
            "confidence": 0.8,
            "suggested_next_step": "Contact the payment provider.",
        }
    )


def turn(*calls: tuple[str, str], text: str = "", finish: str = "tool_calls") -> ChatResult:
    requests = [ToolCallRequest(f"c{i}", name, args) for i, (name, args) in enumerate(calls)]
    return ChatResult(text, requests, finish, Usage(100, 20), "fake", "fake-1", 5)


class ScriptedModel:
    def __init__(self, *turns):
        self.turns = list(turns)
        self.seen: list[tuple[list[dict], str | dict]] = []

    def chat(self, messages, tools, tool_choice="auto"):
        self.seen.append(([dict(m) for m in messages], tool_choice))
        item = self.turns.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def agent(model, **limits) -> TriageAgent:
    return TriageAgent(model, ToolRegistry([Look(ToolContext())]), Limits(**limits))


LOOK = ("look", '{"target": "logs"}')


def test_investigate_then_submit():
    model = ScriptedModel(turn(LOOK), turn(("submit_report", report("t1"))))
    run = agent(model).investigate("Checkout errors")
    assert run.outcome is ReportOutcome.SUBMITTED
    assert run.report.category.value == "dependency_slow"
    assert [c.id for c in run.tool_calls] == ["t1"]
    tool_reply = model.seen[1][0][-1]
    assert tool_reply == {"role": "tool", "tool_call_id": "c0", "content": '[t1] {"saw":"logs"}'}
    assert run.usage.total == 240 and len(run.turns) == 2


def test_tools_in_the_same_turn_run_before_the_report_that_cites_them():
    model = ScriptedModel(turn(("submit_report", report("t1", "t2")), LOOK, LOOK))
    run = agent(model).investigate("x")
    assert run.outcome is ReportOutcome.SUBMITTED
    assert [c.id for c in run.tool_calls] == ["t1", "t2"]


def test_invalid_report_is_sent_back_and_can_be_fixed():
    model = ScriptedModel(
        turn(LOOK),
        turn(("submit_report", report("t9"))),
        turn(("submit_report", report("t1"))),
    )
    run = agent(model).investigate("x")
    assert run.outcome is ReportOutcome.SUBMITTED
    assert run.invalid_reports == [
        "report rejected: evidence cites t9, but no such tool call exists"
    ]
    feedback = model.seen[2][0][-1]["content"]
    assert "no such tool call" in feedback and "call submit_report again" in feedback


def test_schema_errors_and_category_component_mismatch_are_rejected():
    model = ScriptedModel(
        turn(LOOK),
        turn(("submit_report", '{"category": "aliens"}')),
        turn(("submit_report", report("t1", category="no_fault"))),
        turn(("submit_report", report("t1", category="no_fault", component="none"))),
    )
    run = agent(model).investigate("x")
    assert run.outcome is ReportOutcome.SUBMITTED
    assert "category: Input should be" in run.invalid_reports[0]
    assert "component must be 'none'" in run.invalid_reports[1]


def test_prose_and_cut_off_replies_get_a_reminder():
    model = ScriptedModel(
        turn(text="I think it is the database.", finish="stop"),
        turn(text="The root ca", finish="length"),
        turn(LOOK),
        turn(("submit_report", report("t1"))),
    )
    run = agent(model).investigate("x")
    assert run.outcome is ReportOutcome.SUBMITTED
    assert model.seen[1][0][-1]["content"] == prompts.NO_TOOL_CALL_REMINDER
    assert model.seen[2][0][-1]["content"] == prompts.CUT_OFF_REMINDER


def test_tool_budget_forces_a_report_on_the_next_turn():
    model = ScriptedModel(turn(LOOK), turn(LOOK), turn(("submit_report", report("t1", "t2"))))
    run = agent(model, max_tool_calls=2).investigate("x")
    assert run.outcome is ReportOutcome.FORCED
    messages, choice = model.seen[2]
    assert choice == {"type": "function", "function": {"name": "submit_report"}}
    assert "budget used up (2 tool calls)" in messages[-1]["content"]
    assert model.seen[0][1] == "auto"


def test_turn_limit_without_a_report_gives_an_honest_placeholder():
    model = ScriptedModel(turn(LOOK), turn(LOOK), turn(LOOK))
    run = agent(model, max_turns=3).investigate("x")
    assert run.outcome is ReportOutcome.FAILED
    assert run.report.category.value == "inconclusive" and run.report.confidence == 0
    assert run.stop_reason == "turn limit"
    assert model.seen[2][1] != "auto"  # the last turn demanded a report


def test_model_errors_end_the_run_and_are_recorded():
    model = ScriptedModel(turn(LOOK), LLMError("gemini HTTP 503: overloaded", 503))
    run = agent(model).investigate("x")
    assert run.outcome is ReportOutcome.FAILED
    assert run.error == "gemini HTTP 503: overloaded" and run.stop_reason == "model error"
    assert "overloaded" in run.report.summary


def test_repeated_invalid_reports_stop_the_run():
    bad = turn(("submit_report", "{}"))
    run = agent(ScriptedModel(bad, bad, bad, bad)).investigate("x")
    assert run.outcome is ReportOutcome.FAILED
    assert run.stop_reason == "3 invalid reports" and len(run.turns) == 3


def test_submit_schema_is_self_contained():
    schema = submit_tool_schema()["function"]["parameters"]
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text and '"title"' not in text
    assert "inconclusive" in schema["properties"]["category"]["enum"]
    evidence_item = schema["properties"]["evidence"]["items"]
    assert evidence_item["required"] == ["tool_call_id", "finding"]


def test_trace_is_saved_as_json(tmp_path):
    run = agent(ScriptedModel(turn(LOOK), turn(("submit_report", report("t1"))))).investigate("x")
    data = json.loads(save_run(run, tmp_path, label="demo").read_text())
    assert data["outcome"] == "submitted" and data["report"]["category"] == "dependency_slow"
    assert data["tool_calls"][0]["id"] == "t1" and data["usage"]["total_tokens"] == 240
    assert len(data["turns"]) == 2
