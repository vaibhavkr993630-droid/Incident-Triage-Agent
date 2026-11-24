"""Run an evaluation: every scenario x every variant x N repeats, scored and saved.

For each (repeat, scenario): inject the fault once, then let every variant investigate the
same live incident, in a shuffled order so no variant always sees the incident earlier or
later, then recover. Each variant's tools only see telemetry from when the lab was last
confirmed healthy (ToolContext.not_before), so one scenario cannot leak into the next.

Results are appended to evals/results/<eval_id>/results.jsonl as soon as each run is scored,
so an interrupted evaluation resumes where it stopped. Full traces go to
evals/runs/<eval_id>/ (git-ignored).

Free-tier safety: a model-request budget stops the evaluation before it is exceeded, and two
model errors in a row (typically a used-up daily quota) stop it too.
"""

import dataclasses
import json
import random
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from evals.baseline import run_baseline
from evals.scoring import score_run
from faults.models import Scenario, load_scenarios
from faults.runner import Lab, inject, recover
from triage.agent import AgentRun, Limits, TriageAgent
from triage.llm import ChatModel
from triage.tools import TOOL_CLASSES, ToolContext
from triage.tools.base import ToolRegistry
from triage.tools.runbooks import SearchRunbooks
from triage.trace import save_run

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "evals" / "results"
RUNS_DIR = REPO_ROOT / "evals" / "runs"
# A run rarely needs more than this many model requests; used to stop before the budget.
REQUESTS_PER_RUN_ESTIMATE = 16
# A provider or network error says nothing about the agent: retry that run once, after a
# pause, while the fault is still active.
ERROR_RETRY_PAUSE_S = 20


def registry_for(ctx: ToolContext, runbooks: bool) -> ToolRegistry:
    classes = [c for c in TOOL_CLASSES if runbooks or c is not SearchRunbooks]
    return ToolRegistry([cls(ctx) for cls in classes])


Variant = Callable[[ChatModel, ToolContext, str], AgentRun]

VARIANTS: dict[str, Variant] = {
    "agent": lambda model, ctx, alert: TriageAgent(model, registry_for(ctx, True)).investigate(
        alert
    ),
    "agent_no_runbooks": lambda model, ctx, alert: TriageAgent(
        model, registry_for(ctx, False)
    ).investigate(alert),
    "baseline": lambda model, ctx, alert: run_baseline(model, registry_for(ctx, False), alert),
    "baseline_no_guard": lambda model, ctx, alert: run_baseline(
        model, registry_for(dataclasses.replace(ctx, injection_guard=False), False), alert
    ),
    # Ablation: the full agent with the prompt-injection detector switched off.
    "agent_no_guard": lambda model, ctx, alert: TriageAgent(
        model, registry_for(dataclasses.replace(ctx, injection_guard=False), True)
    ).investigate(alert),
}


def model_requests(run: AgentRun) -> int:
    return sum(t.attempts for t in run.turns) or 1


def git_commit() -> str:
    out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                         capture_output=True, text=True)  # fmt: skip
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT,
                           capture_output=True, text=True).stdout.strip()  # fmt: skip
    return out.stdout.strip() + ("+uncommitted" if dirty else "")


@dataclass
class EvalConfig:
    eval_id: str
    scenarios: list[str]
    variants: list[str]
    repeats: int = 1
    max_requests: int = 400
    seed: int = 7
    primary_model: str | None = None
    notes: dict = field(default_factory=dict)


def load_done(path: Path) -> set[tuple[int, str, str]]:
    if not path.exists():
        return set()
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return {(r["repeat"], r["scenario"], r["variant"]) for r in rows}


def run_eval(config: EvalConfig, model: ChatModel, lab: Lab | None = None, log=print) -> Path:
    lab = lab or Lab(log=log)
    scenarios: dict[str, Scenario] = load_scenarios()
    out_dir = RESULTS_DIR / config.eval_id
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    meta_path = out_dir / "meta.json"
    if not meta_path.exists():
        meta = {
            "eval_id": config.eval_id,
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "commit": git_commit(),
            "scenarios": config.scenarios,
            "variants": config.variants,
            "repeats": config.repeats,
            "seed": config.seed,
            "primary_model": config.primary_model,
            "limits": Limits().__dict__,
            **config.notes,
        }
        meta_path.write_text(json.dumps(meta, indent=2))
    done = load_done(results_path)
    used, consecutive_errors = 0, 0

    for repeat in range(1, config.repeats + 1):
        for scenario_id in config.scenarios:
            pending = [v for v in config.variants if (repeat, scenario_id, v) not in done]
            if not pending:
                continue
            if used + REQUESTS_PER_RUN_ESTIMATE > config.max_requests:
                log(f"stopping: model-request budget {config.max_requests} would be exceeded")
                return results_path
            scenario = scenarios[scenario_id]
            random.Random(f"{config.seed}-{scenario_id}-{repeat}").shuffle(pending)
            window = inject(lab, scenario)
            try:
                for variant in pending:
                    if used + REQUESTS_PER_RUN_ESTIMATE > config.max_requests:
                        break
                    ctx = ToolContext(not_before=window.healthy_since)
                    run = VARIANTS[variant](model, ctx, scenario.alert)
                    used += model_requests(run)
                    retried = False
                    if run.error:
                        log(f"  provider error in {variant} ({run.error}); retrying once")
                        time.sleep(ERROR_RETRY_PAUSE_S)
                        run = VARIANTS[variant](model, ToolContext(not_before=window.healthy_since),
                                                scenario.alert)  # fmt: skip
                        used += model_requests(run)
                        retried = True
                    trace = save_run(
                        run, RUNS_DIR / config.eval_id, label=f"{scenario_id}-{variant}"
                    )
                    score = score_run(run, scenario, config.primary_model)
                    row = {
                        "eval_id": config.eval_id,
                        "repeat": repeat,
                        "variant": variant,
                        **score.as_row(),
                        "model_requests": model_requests(run),
                        "retried": retried,
                        "trace": str(trace.relative_to(REPO_ROOT)),
                    }
                    with results_path.open("a") as f:
                        f.write(json.dumps(row) + "\n")
                    mark = "ok " if score.correct else "XX "
                    cost = f"{score.tool_calls} tools, {score.tokens} tok, {score.seconds}s"
                    log(f"  {mark}{variant:<18} -> {score.category}@{score.component} ({cost})")
                    consecutive_errors = consecutive_errors + 1 if run.error else 0
                    if consecutive_errors >= 2:
                        log(f"stopping: two model errors in a row (last: {run.error})")
                        return results_path
            finally:
                recover(lab, scenario)
                lab.wait_for_baseline()
    log(f"done: {results_path} ({used} model requests)")
    return results_path
