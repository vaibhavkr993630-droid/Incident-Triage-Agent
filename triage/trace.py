"""Save an AgentRun as a JSON trace: every turn, tool call, token count and the report."""

import json
import uuid
from dataclasses import asdict
from pathlib import Path

from triage.agent import AgentRun

RUNS_DIR = Path("runs")


def run_to_dict(run: AgentRun) -> dict:
    return {
        "alert": run.alert,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "seconds": run.seconds,
        "outcome": run.outcome.value,
        "stop_reason": run.stop_reason,
        "error": run.error,
        "limits": asdict(run.limits),
        "usage": {
            "prompt_tokens": run.usage.prompt_tokens,
            "completion_tokens": run.usage.completion_tokens,
            "total_tokens": run.usage.total,
        },  # fmt: skip
        "report": run.report.model_dump(mode="json") if run.report else None,
        "invalid_reports": run.invalid_reports,
        "turns": [
            {
                **asdict(t),
                "usage": {"prompt": t.usage.prompt_tokens, "completion": t.usage.completion_tokens},
            }  # fmt: skip
            for t in run.turns
        ],
        "tool_calls": [asdict(c) for c in run.tool_calls],
    }


def save_run(run: AgentRun, directory: Path = RUNS_DIR, label: str = "") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = run.started_at.strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{label + '-' if label else ''}{uuid.uuid4().hex[:6]}.json"
    path = directory / name
    path.write_text(json.dumps(run_to_dict(run), indent=2, default=str))
    return path
