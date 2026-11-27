"""The investigation console's HTTP API.

    uv run uvicorn triage.api:app --port 8100

Read-only views of what is on disk, plus one action:

    GET  /api/runs                      saved investigations, newest first (summaries)
    GET  /api/runs/{source}/{name}      one investigation's full trace
    GET  /api/evals                     evaluations, with per-variant headline numbers
    GET  /api/evals/{eval_id}           one evaluation: metadata and every scored row
    POST /api/investigations            start an investigation (one at a time)
    GET  /api/investigations/{job_id}   its status, and the run id once it has finished

Traces come from runs/ (source "manual") and evals/runs/<eval_id>/ (source = the eval id).
If web/dist exists (the built frontend), it is served at /.

Starting an investigation calls the model, so it uses the free-tier quota; only one may run
at a time.
"""

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[1]
MANUAL_RUNS = REPO_ROOT / "runs"
EVAL_RUNS = REPO_ROOT / "evals" / "runs"
EVAL_RESULTS = REPO_ROOT / "evals" / "results"
WEB_DIST = REPO_ROOT / "web" / "dist"
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def run_summary(source: str, path: Path) -> dict:
    data = json.loads(path.read_text())
    report = data.get("report") or {}
    return {
        "id": f"{source}/{path.stem}",
        "source": source,
        "started_at": data.get("started_at"),
        "alert": (data.get("alert") or "")[:200],
        "category": report.get("category"),
        "component": report.get("component"),
        "confidence": report.get("confidence"),
        "outcome": data.get("outcome"),
        "turns": len(data.get("turns", [])),
        "tool_calls": len(data.get("tool_calls", [])),
        "tokens": (data.get("usage") or {}).get("total_tokens"),
        "seconds": data.get("seconds"),
    }


def list_runs(manual_dir: Path, eval_dir: Path) -> list[dict]:
    found = [("manual", p) for p in manual_dir.glob("*.json")] if manual_dir.is_dir() else []
    if eval_dir.is_dir():
        for sub in eval_dir.iterdir():
            if sub.is_dir():
                found += [(sub.name, p) for p in sub.glob("*.json")]
    summaries = []
    for source, path in found:
        try:
            summaries.append(run_summary(source, path))
        except (ValueError, OSError):
            continue  # a half-written or foreign file is skipped, not fatal
    return sorted(summaries, key=lambda s: s["started_at"] or "", reverse=True)


def eval_headline(rows: list[dict]) -> dict:
    by_variant: dict[str, list[dict]] = {}
    for r in rows:
        by_variant.setdefault(r["variant"], []).append(r)
    headline = {}
    for variant, rs in by_variant.items():
        ok = [r for r in rs if not r.get("error")]
        headline[variant] = {
            "runs": len(rs),
            "correct": sum(r["correct"] for r in ok),
            "scored": len(ok),
            "avg_tokens": round(sum(r["tokens"] for r in ok) / len(ok)) if ok else None,
        }
    return headline


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class InvestigationIn(BaseModel):
    alert: str = Field(min_length=10, max_length=1000)
    not_before: datetime | None = None
    max_tool_calls: int = Field(default=20, ge=1, le=40)


def create_app(
    manual_dir: Path = MANUAL_RUNS,
    eval_dir: Path = EVAL_RUNS,
    results_dir: Path = EVAL_RESULTS,
    web_dist: Path = WEB_DIST,
    run_investigation=None,
) -> FastAPI:
    """Build the app. Directories and the investigation function are injectable for tests."""
    app = FastAPI(title="Incident triage console")
    jobs: dict[str, dict] = {}
    busy = threading.Lock()

    def safe(*parts: str) -> None:
        if not all(_SAFE.match(p) for p in parts):
            raise HTTPException(404, "not found")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/runs")
    def runs(limit: int = 200) -> list[dict]:
        return list_runs(manual_dir, eval_dir)[: max(1, min(limit, 1000))]

    @app.get("/api/runs/{source}/{name}")
    def run(source: str, name: str) -> dict:
        safe(source, name)
        path = (manual_dir if source == "manual" else eval_dir / source) / f"{name}.json"
        if not path.is_file():
            raise HTTPException(404, "run not found")
        return {"id": f"{source}/{name}", **json.loads(path.read_text())}

    @app.get("/api/evals")
    def evals() -> list[dict]:
        out = []
        for meta_path in sorted(results_dir.glob("*/meta.json"), reverse=True):
            rows_path = meta_path.parent / "results.jsonl"
            rows = read_rows(rows_path) if rows_path.exists() else []
            out.append({"id": meta_path.parent.name, "meta": json.loads(meta_path.read_text()),
                        "headline": eval_headline(rows)})  # fmt: skip
        return out

    @app.get("/api/evals/{eval_id}")
    def evaluation(eval_id: str) -> dict:
        safe(eval_id)
        folder = results_dir / eval_id
        if not (folder / "meta.json").is_file():
            raise HTTPException(404, "evaluation not found")
        rows_path = folder / "results.jsonl"
        rows = read_rows(rows_path) if rows_path.exists() else []
        for r in rows:  # trace paths -> console run ids
            r["run_id"] = f"{eval_id}/{Path(r['trace']).stem}" if r.get("trace") else None
        return {"id": eval_id, "meta": json.loads((folder / "meta.json").read_text()),
                "rows": rows}  # fmt: skip

    @app.post("/api/investigations", status_code=202)
    def start(body: InvestigationIn) -> dict:
        if not busy.acquire(blocking=False):
            raise HTTPException(409, "an investigation is already running")
        job_id = uuid.uuid4().hex[:12]
        jobs[job_id] = {"id": job_id, "status": "running", "alert": body.alert}

        def work():
            try:
                runner = run_investigation or default_investigation
                jobs[job_id].update(status="done", run_id=runner(body, manual_dir))
            # SystemExit too: build_client exits when no API key is configured.
            except (Exception, SystemExit) as exc:  # noqa: BLE001 - reported, never crashes
                jobs[job_id].update(status="failed", error=f"{type(exc).__name__}: {exc}")
            finally:
                busy.release()

        threading.Thread(target=work, daemon=True).start()
        return jobs[job_id]

    @app.get("/api/investigations/{job_id}")
    def job(job_id: str) -> dict:
        if job_id not in jobs:
            raise HTTPException(404, "job not found")
        return jobs[job_id]

    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app


def default_investigation(body: InvestigationIn, manual_dir: Path) -> str:
    """Run the real agent and save its trace; returns the console run id."""
    from triage.__main__ import build_client
    from triage.agent import Limits, TriageAgent
    from triage.tools import ToolContext, build_registry
    from triage.trace import save_run

    registry = build_registry(ToolContext(not_before=body.not_before))
    agent = TriageAgent(build_client(), registry, Limits(max_tool_calls=body.max_tool_calls))
    path = save_run(agent.investigate(body.alert), manual_dir, label="console")
    return f"manual/{path.stem}"


app = create_app()
