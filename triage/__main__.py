"""Run the triage agent from the command line.

    uv run python -m triage investigate "Checkout errors: ..." [--not-before ISO-TIME]
    uv run python -m triage models          # models your configured key can use

Configuration (environment or a .env file in the repo root, which git ignores):
    GEMINI_API_KEY          primary provider (Google AI Studio free tier)
    GROQ_API_KEY            optional fallback provider
    TRIAGE_PROVIDER         gemini (default) | groq | ollama
    TRIAGE_MODEL            override the primary model name
    TRIAGE_FALLBACK_PROVIDER, TRIAGE_FALLBACK_MODEL
                            default: groq if GROQ_API_KEY is set, else a second gemini model
    TRIAGE_REASONING_EFFORT for "thinking" models, e.g. low
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx

from triage.agent import Limits, TriageAgent
from triage.llm import OpenAICompatibleClient, load_dotenv, providers_from_env
from triage.tools import ToolContext, build_registry
from triage.trace import save_run

REPO_ROOT = Path(__file__).resolve().parents[1]


def build_client() -> OpenAICompatibleClient:
    load_dotenv(REPO_ROOT / ".env")
    primary, fallback = providers_from_env()
    if primary is None:
        name = os.environ.get("TRIAGE_PROVIDER", "gemini")
        sys.exit(f"No API key for provider '{name}'. Put it in {REPO_ROOT / '.env'}.")
    return OpenAICompatibleClient(primary, fallback)


def print_report(run, path: Path) -> None:
    r = run.report
    print(f"\n{'=' * 72}\n{r.category.value}  @ {r.component.value}   "
          f"(confidence {r.confidence:.2f}, outcome {run.outcome.value})\n{'=' * 72}")  # fmt: skip
    print(r.summary)
    print("\nEvidence:")
    for e in r.evidence:
        print(f"  [{e.tool_call_id}] {e.finding}")
    if r.ruled_out:
        print("Ruled out:")
        for x in r.ruled_out:
            print(f"  - {x.hypothesis}: {x.reason}")
    print(f"\nNext step: {r.suggested_next_step}")
    u = run.usage
    print(f"\n{len(run.turns)} turns, {len(run.tool_calls)} tool calls, {u.total} tokens "
          f"({u.prompt_tokens} in / {u.completion_tokens} out), {run.seconds}s, "
          f"stop: {run.stop_reason}")  # fmt: skip
    if run.error:
        print(f"error: {run.error}")
    print(f"trace: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m triage")
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("investigate")
    inv.add_argument("alert")
    inv.add_argument("--not-before", help="ISO time; tools never look earlier than this")
    inv.add_argument("--max-tool-calls", type=int, default=Limits.max_tool_calls)
    sub.add_parser("models")
    args = parser.parse_args(argv)

    client = build_client()
    if args.command == "models":
        for provider in client.providers:
            resp = httpx.get(
                f"{provider.base_url}/models",
                headers={"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {},
                timeout=20,
            )
            resp.raise_for_status()
            names = sorted(m["id"] for m in resp.json().get("data", []))
            print(f"{provider.name} (configured: {provider.model}):")
            for name in names:
                print(f"  {name}")
        return 0

    not_before = datetime.fromisoformat(args.not_before) if args.not_before else None
    registry = build_registry(ToolContext(not_before=not_before))
    agent = TriageAgent(client, registry, Limits(max_tool_calls=args.max_tool_calls))
    run = agent.investigate(args.alert)
    print_report(run, save_run(run, REPO_ROOT / "runs"))
    return 0 if run.outcome.value != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
