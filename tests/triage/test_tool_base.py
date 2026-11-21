"""Registry and session mechanics, using small local tools (no network)."""

import json

from pydantic import BaseModel, ConfigDict, Field

from triage.tools import build_registry
from triage.tools.base import (
    MAX_RESULT_CHARS,
    Tool,
    ToolContext,
    ToolError,
    ToolRegistry,
    ToolSession,
    fit_list,
    to_text,
)


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    times: int = Field(default=1, ge=1, le=5)


class Echo(Tool):
    name = "echo"
    description = "Repeat text."
    Args = EchoArgs

    def run(self, args):
        if args.text == "fail":
            raise ToolError("backend said no")
        if args.text == "crash":
            raise RuntimeError("bug")
        return {"out": [args.text] * args.times}


def session(max_calls: int = 20) -> ToolSession:
    return ToolSession(ToolRegistry([Echo(ToolContext())]), max_calls=max_calls)


def test_calls_get_sequential_ids_and_are_recorded():
    s = session()
    a, b = s.call("echo", {"text": "hi"}), s.call("echo", '{"text": "yo", "times": 2}')
    assert (a.id, b.id) == ("t1", "t2")
    assert json.loads(b.result) == {"out": ["yo", "yo"]}
    assert s.get("t2") is b and s.get("t9") is None
    assert not a.is_error and b.arguments == {"text": "yo", "times": 2}


def test_bad_arguments_come_back_as_readable_errors():
    s = session()
    for raw, expected in [
        ("{not json", "not valid JSON"),
        ('["a list"]', "must be a JSON object"),
        ({"times": 2}, "text: Field required"),
        ({"text": "x", "times": 9}, "times: Input should be less than or equal to 5"),
        ({"text": "x", "colour": "red"}, "colour: Extra inputs are not permitted"),
    ]:
        call = s.call("echo", raw)
        assert call.is_error
        assert expected in json.loads(call.result)["error"]


def test_numbers_sent_as_strings_are_accepted():
    """Models often send "3" instead of 3; lax validation accepts it."""
    assert not session().call("echo", {"text": "x", "times": "3"}).is_error


def test_unknown_tool_lists_the_real_ones():
    call = session().call("delete_database", {})
    assert call.is_error
    assert "Available tools: echo" in json.loads(call.result)["error"]


def test_tool_failures_never_raise():
    s = session()
    assert json.loads(s.call("echo", {"text": "fail"}).result) == {"error": "backend said no"}
    crash = s.call("echo", {"text": "crash"})
    assert crash.is_error and "internal tool error: RuntimeError" in crash.result
    assert "bug" not in crash.result  # internals are not leaked


def test_budget_stops_calls_and_says_what_to_do():
    s = session(max_calls=2)
    s.call("echo", {"text": "a"})
    s.call("echo", {"text": "b"})
    over = s.call("echo", {"text": "c"})
    assert over.is_error and over.id == "t3"
    assert "budget" in over.result and "write the report" in over.result


def test_oversized_results_are_capped_and_flagged():
    text, truncated = to_text({"blob": "x" * (MAX_RESULT_CHARS * 2)})
    assert truncated and len(text) == MAX_RESULT_CHARS
    assert text.endswith(f"limit {MAX_RESULT_CHARS}]")


def test_fit_list_drops_the_tail_and_counts_it():
    payload = {"items": [{"n": i, "pad": "y" * 50} for i in range(500)]}
    fitted = fit_list(payload, "items", budget=2000)
    assert len(to_text(fitted)[0]) <= 2000
    assert fitted["items"][0]["n"] == 0  # most important (first) items are kept
    assert fitted["omitted_items"] == 500 - len(fitted["items"])
    assert "omitted_items" not in fit_list({"items": [1, 2]}, "items")


def test_real_tool_schemas_are_clean_function_definitions():
    schemas = build_registry().schemas()
    names = [s["function"]["name"] for s in schemas]
    assert names == [
        "service_health", "query_logs", "query_metrics", "list_metrics", "list_recent_changes",
        "search_runbooks",
    ]  # fmt: skip
    for s in schemas:
        assert s["type"] == "function"
        params = s["function"]["parameters"]
        assert params["type"] == "object"
        assert "title" not in json.dumps(params)
        assert params.get("additionalProperties") is False
    query = next(s for s in schemas if s["function"]["name"] == "query_metrics")
    assert query["function"]["parameters"]["required"] == ["query"]


def test_window_start_never_reaches_before_not_before():
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    free = ToolContext()
    assert free.window_start(now, 10) == now - timedelta(minutes=10)
    isolated = ToolContext(not_before=now - timedelta(minutes=2))
    assert isolated.window_start(now, 10) == now - timedelta(minutes=2)
    assert isolated.window_start(now, 1) == now - timedelta(minutes=1)
