"""Export investigations and evaluations as static JSON for the console's demo build.

    uv run python -m triage.export                       # everything on disk
    uv run python -m triage.export --evals 2026-11-21-v1 --no-manual
    cd web && npm run build:static                       # site that reads web/public/data

The files mirror the console API exactly (runs.json = GET /api/runs, and so on), so the
same frontend renders them with no backend: a recruiter can browse real recorded
investigations on a static host without running the lab or spending model quota.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

from triage.api import EVAL_RESULTS, EVAL_RUNS, MANUAL_RUNS, REPO_ROOT, create_app

OUT = REPO_ROOT / "web" / "public" / "data"


def export(out: Path, evals: list[str] | None, manual: bool) -> dict[str, int]:
    from fastapi.testclient import TestClient

    # Serve from the real API code, so the exported files cannot drift from the live API.
    client = TestClient(create_app(MANUAL_RUNS, EVAL_RUNS, EVAL_RESULTS, out / "none"))
    eval_list = [e for e in client.get("/api/evals").json() if evals is None or e["id"] in evals]
    keep_sources = {e["id"] for e in eval_list} | ({"manual"} if manual else set())
    runs = [r for r in client.get("/api/runs?limit=1000").json() if r["source"] in keep_sources]

    if out.exists():
        shutil.rmtree(out)
    (out / "runs").mkdir(parents=True)
    (out / "evals").mkdir()
    (out / "runs.json").write_text(json.dumps(runs))
    (out / "evals.json").write_text(json.dumps(eval_list))
    for r in runs:
        path = out / "runs" / f"{r['id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(client.get(f"/api/runs/{r['id']}").json()))
    for e in eval_list:
        detail = client.get(f"/api/evals/{e['id']}").json()
        (out / "evals" / f"{e['id']}.json").write_text(json.dumps(detail))
    return {"runs": len(runs), "evals": len(eval_list)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m triage.export")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--evals", nargs="*", help="evaluation ids to include (default: all)")
    parser.add_argument("--no-manual", action="store_true", help="leave out runs/ (manual runs)")
    args = parser.parse_args(argv)
    counts = export(args.out, args.evals, manual=not args.no_manual)
    print(f"exported {counts['runs']} runs and {counts['evals']} evaluations to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
