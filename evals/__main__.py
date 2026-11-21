"""Run and summarize evaluations.

    uv run python -m evals run --id 2026-11-22-full --repeats 1
    uv run python -m evals run --id quick --scenarios payment_slow no_fault \
    --variants agent baseline
    uv run python -m evals report evals/results/<id>/results.jsonl
    uv run python -m evals rescore evals/results/<id>/results.jsonl   # from traces, no model

The lab must be running (docker compose up). Re-running the same --id resumes it.
"""

import argparse
import sys
from pathlib import Path

from evals.report import rescore, write_summary
from evals.runner import VARIANTS, EvalConfig, run_eval
from faults.models import load_scenarios


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--id", required=True, help="evaluation id; reuse it to resume")
    run.add_argument("--scenarios", nargs="*", default=None)
    run.add_argument("--variants", nargs="*", default=list(VARIANTS), choices=list(VARIANTS))
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--max-requests", type=int, default=400)
    for name in ("report", "rescore"):
        sub.add_parser(name).add_argument("results", type=Path)
    args = parser.parse_args(argv)

    if args.command == "rescore":
        print(f"rescored {rescore(args.results)} rows")
        print(write_summary(args.results).read_text())
        return 0
    if args.command == "report":
        print(write_summary(args.results).read_text())
        return 0

    from triage.__main__ import build_client  # loads .env; only needed for real runs

    client = build_client()
    scenarios = args.scenarios or list(load_scenarios())
    config = EvalConfig(
        eval_id=args.id,
        scenarios=scenarios,
        variants=args.variants,
        repeats=args.repeats,
        max_requests=args.max_requests,
        primary_model=client.providers[0].model,
        notes={"fallback_model": client.providers[1].model if len(client.providers) > 1 else None},
    )
    results = run_eval(config, client)
    print(write_summary(results).read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
