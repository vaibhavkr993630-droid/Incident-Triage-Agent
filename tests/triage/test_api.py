"""The console API on temporary directories with a fake investigation (no model, no lab)."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from triage.api import create_app


def trace(alert: str, started: str, category: str = "dependency_slow") -> dict:
    return {
        "alert": alert, "started_at": started, "outcome": "submitted", "seconds": 12.5,
        "usage": {"total_tokens": 1234},
        "report": {"category": category, "component": "payment-provider", "confidence": 0.9},
        "turns": [{"number": 1}], "tool_calls": [{"id": "t1"}, {"id": "t2"}],
    }  # fmt: skip


@pytest.fixture
def dirs(tmp_path):
    manual, evals_runs, results = tmp_path / "runs", tmp_path / "evals_runs", tmp_path / "results"
    manual.mkdir()
    (evals_runs / "ev1").mkdir(parents=True)
    (results / "ev1").mkdir(parents=True)
    (manual / "20261122-100000-a.json").write_text(
        json.dumps(trace("manual one", "2026-11-22T10:00:00"))
    )
    (evals_runs / "ev1" / "20261122-090000-b.json").write_text(
        json.dumps(trace("eval one", "2026-11-22T09:00:00", "no_fault"))
    )
    (manual / "broken.json").write_text("{not json")
    (results / "ev1" / "meta.json").write_text(json.dumps({"eval_id": "ev1"}))
    row = {"variant": "agent", "correct": True, "tokens": 100,
           "trace": "evals/runs/ev1/20261122-090000-b.json"}  # fmt: skip
    (results / "ev1" / "results.jsonl").write_text(
        json.dumps(row) + "\n" + json.dumps({**row, "correct": False, "error": "x"}) + "\n"
    )
    return manual, evals_runs, results


def client(dirs, runner=None) -> TestClient:
    manual, evals_runs, results = dirs
    return TestClient(create_app(manual, evals_runs, results, results / "no-web",
                                 run_investigation=runner))  # fmt: skip


def test_runs_are_listed_newest_first_and_broken_files_skipped(dirs):
    runs = client(dirs).get("/api/runs").json()
    assert [r["id"] for r in runs] == ["manual/20261122-100000-a", "ev1/20261122-090000-b"]
    assert runs[0] | {} == runs[0] and runs[0]["tool_calls"] == 2 and runs[0]["tokens"] == 1234


def test_one_run_and_path_traversal_is_refused(dirs):
    c = client(dirs)
    run = c.get("/api/runs/ev1/20261122-090000-b").json()
    assert run["id"] == "ev1/20261122-090000-b" and run["report"]["category"] == "no_fault"
    assert c.get("/api/runs/manual/missing").status_code == 404
    assert c.get("/api/runs/..%2F..%2Fetc/passwd").status_code == 404
    assert c.get("/api/runs/manual/.hidden").status_code == 404


def test_evaluations_with_headline_and_rows(dirs):
    c = client(dirs)
    listed = c.get("/api/evals").json()
    assert listed[0]["id"] == "ev1"
    # The provider-error row is excluded from the score, as in the markdown report.
    assert listed[0]["headline"]["agent"] == {"runs": 2, "correct": 1, "scored": 1,
                                              "avg_tokens": 100}  # fmt: skip
    detail = c.get("/api/evals/ev1").json()
    assert detail["rows"][0]["run_id"] == "ev1/20261122-090000-b"
    assert c.get("/api/evals/nope").status_code == 404


def test_investigations_run_in_the_background_one_at_a_time(dirs):
    def slow_runner(body, manual_dir):
        time.sleep(0.3)
        return "manual/new-run"

    c = client(dirs, slow_runner)
    started = c.post("/api/investigations", json={"alert": "Checkout errors are high"})
    assert started.status_code == 202 and started.json()["status"] == "running"
    second = c.post("/api/investigations", json={"alert": "Another alert text here"})
    assert second.status_code == 409
    job_id = started.json()["id"]
    for _ in range(50):
        job = c.get(f"/api/investigations/{job_id}").json()
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job == {"id": job_id, "status": "done", "alert": "Checkout errors are high",
                   "run_id": "manual/new-run"}  # fmt: skip


def test_a_failing_investigation_is_reported_not_raised(dirs):
    def no_key(body, manual_dir):
        raise SystemExit("No API key for provider 'gemini'.")

    c = client(dirs, no_key)
    job_id = c.post("/api/investigations", json={"alert": "Checkout errors are high"}).json()["id"]
    for _ in range(50):
        job = c.get(f"/api/investigations/{job_id}").json()
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "failed" and "No API key" in job["error"]
    # The lock was released: a new investigation can start.
    assert c.post("/api/investigations", json={"alert": "Checkout errors again"}).status_code == 202


def test_short_alerts_are_rejected(dirs):
    assert client(dirs).post("/api/investigations", json={"alert": "hi"}).status_code == 422
