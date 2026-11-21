import json

from evals.report import summarize
from evals.scoring import number_is_grounded, numbers_in, score_run
from faults.models import load_scenarios
from triage.agent import AgentRun, Limits, Turn
from triage.llm import Usage
from triage.report import Report, ReportOutcome
from triage.tools.base import ToolCall, utc_now

SCENARIOS = load_scenarios()


def make_run(category, component, findings, results, confidence=0.9, model="m1") -> AgentRun:
    run = AgentRun(alert="x", started_at=utc_now(), limits=Limits())
    run.tool_calls = [
        ToolCall(f"t{i}", "query_logs", {}, json.dumps(r), False, 5)
        for i, r in enumerate(results, 1)
    ]
    run.turns = [Turn(1, "p", model, 10, 1, Usage(100, 10), "stop", "", [])]
    run.report = Report.model_validate(
        {
            "category": category,
            "component": component,
            "summary": "A summary that is long enough to be valid.",
            "evidence": [{"tool_call_id": i, "finding": f} for i, f in findings],
            "confidence": confidence,
            "suggested_next_step": "Do something.",
        }
    )
    run.outcome = ReportOutcome.SUBMITTED
    run.finished_at = utc_now()
    return run


def test_numbers_ignore_tool_ids_times_and_versions_and_read_units():
    assert numbers_in("[t3] p95 is 4.8 s and 504 errors") == [4.8, 504.0]
    # Found in the first real evaluation: numbers glued to units were misread as "0.0".
    assert numbers_in("latency ~0.09s, 48ms, 83%") == [0.09, 48.0, 83.0]
    assert numbers_in("deployed at 19:02:49 on 2026-11-21 as v1.8.0") == []
    assert number_is_grounded(4.8, [4.76])  # rounded
    assert number_is_grounded(3082.8, [3.0828])  # ms vs s
    assert number_is_grounded(83, [0.83])  # percent vs ratio
    assert not number_is_grounded(12.0, [4.76, 504.0])


def test_correct_diagnosis_with_grounded_and_invented_numbers():
    run = make_run(
        "dependency_slow",
        "payment-provider",
        [("t1", "provider p95 is 4.9 s"), ("t1", "timeouts at 2.0 s and 77 errors")],
        [{"p95": 4.87, "timeout_s": 2.0}],
    )
    score = score_run(run, SCENARIOS["payment_slow"], primary_model="m1")
    assert score.correct and score.category_correct and score.component_correct
    assert (score.numbers_quoted, score.numbers_grounded) == (3, 2)
    assert (score.numbers_miscited, score.numbers_unsupported) == (0, 1)  # 77 was invented
    assert not score.false_alarm and not score.fallback_used


def test_right_category_wrong_component_is_not_correct():
    run = make_run("dependency_slow", "shop-api", [("t1", "it is slow")], [{}])
    score = score_run(run, SCENARIOS["payment_slow"])
    assert score.category_correct and not score.component_correct and not score.correct


def test_false_alarm_on_the_control_and_fallback_detection():
    run = make_run("db_slow_query", "postgres", [("t1", "it is slow")], [{}], model="other")
    score = score_run(run, SCENARIOS["no_fault"], primary_model="m1")
    assert score.false_alarm and not score.correct and score.fallback_used
    ok = score_run(make_run("no_fault", "none", [("t1", "all ok")], [{}]), SCENARIOS["no_fault"])
    assert ok.correct and not ok.false_alarm


def test_summary_tables():
    rows = []
    for variant, scenario, correct in [
        ("agent", "payment_slow", True),
        ("agent", "no_fault", True),
        ("baseline", "payment_slow", False),
        ("baseline", "no_fault", False),
    ]:
        rows.append({
            "variant": variant, "scenario": scenario, "correct": correct,
            "category_correct": correct, "expected_category": scenario if scenario == "no_fault"
            else "dependency_slow", "false_alarm": scenario == "no_fault" and not correct,
            "outcome": "submitted", "numbers_quoted": 2, "numbers_grounded": 1,
            "numbers_miscited": 1, "numbers_unsupported": 0,
            "tool_calls": 6, "tokens": 1000, "seconds": 10, "confidence": 0.9,
            "category": "x" if not correct else "dependency_slow",
            "component": "y" if not correct else "payment-provider",
        })  # fmt: skip
    text = summarize(rows)
    assert "| `agent` | 2 | 100% | 100% | 0/1 | 0 | 0 | 2 / 2 / 0 of 4 (50%) |" in text
    assert "| `baseline` | 2 | 0% | 0% | 1/1 |" in text
    assert "| `payment_slow` | 1/1 | 0/1 (x@y) |" in text
    errored = summarize(rows + [{**rows[0], "error": "ConnectError", "correct": False}])
    assert "| `agent` | 2 | 100% | 100% | 0/1 | 0 | 1 |" in errored  # excluded, counted apart


def test_numbers_from_another_call_are_mis_cited_and_query_windows_count():
    from evals.scoring import ground_numbers

    calls = {"t1": ('{"depth": 48}', '{"minutes": 15}'), "t2": ('{"rate": 0.82}', "{}")}
    g = ground_numbers([("t1", "depth rose to 48 over 15 minutes, intake 0.82/s, 9 workers")],
                       calls)  # fmt: skip
    assert g == {"quoted": 4, "grounded": 2, "miscited": 1, "unsupported": 1}
