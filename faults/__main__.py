"""Command line for the fault injector.

uv run python -m faults list                 scenarios and their true categories
uv run python -m faults run <id> [<id> ...]  inject, verify signals, recover
uv run python -m faults run --all
uv run python -m faults baseline             is the lab healthy right now?
uv run python -m faults recover-all          undo every fault (after a crashed run)
"""

import argparse
import sys

from faults.models import load_scenarios
from faults.runner import Lab, run_scenario


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m faults")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("ids", nargs="*")
    run.add_argument("--all", action="store_true")
    sub.add_parser("baseline")
    sub.add_parser("recover-all")
    args = parser.parse_args(argv)

    scenarios = load_scenarios()
    lab = Lab()

    if args.command == "list":
        for s in scenarios.values():
            gt = s.ground_truth
            print(f"{s.id:<28} {gt.category:<20} {gt.component:<17} {s.title}")
        return 0

    if args.command == "baseline":
        problems = lab.baseline_problems()
        for p in problems:
            print(f"- {p}")
        print("at baseline" if not problems else "NOT at baseline")
        return 0 if not problems else 1

    if args.command == "recover-all":
        lab.recover_all()
        problems = lab.wait_for_baseline()
        print("at baseline" if not problems else f"still not at baseline: {problems}")
        return 0 if not problems else 1

    ids = list(scenarios) if args.all else args.ids
    unknown = [i for i in ids if i not in scenarios]
    if unknown or not ids:
        parser.error(f"unknown or missing scenario ids: {unknown or '(none given)'}")
    failed = [i for i in ids if not run_scenario(lab, scenarios[i]).ok]
    print(f"\n{len(ids) - len(failed)}/{len(ids)} scenarios verified")
    if failed:
        print("failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
