"""Try the agent's tools by hand, exactly as the agent will call them.

uv run python -m triage.tools list
uv run python -m triage.tools schema
uv run python -m triage.tools call service_health
uv run python -m triage.tools call query_logs '{"level": "error", "minutes": 5}'
"""

import json
import sys

from triage.tools import ToolSession, build_registry


def main(argv: list[str]) -> int:
    registry = build_registry()
    if not argv or argv[0] == "list":
        for tool in registry.tools.values():
            print(f"{tool.name:<20} {tool.description[:90]}...")
        return 0
    if argv[0] == "schema":
        print(json.dumps(registry.schemas(), indent=2))
        return 0
    if argv[0] == "call" and len(argv) >= 2:
        call = ToolSession(registry).call(argv[1], argv[2] if len(argv) > 2 else None)
        print(json.dumps(json.loads(call.result), indent=2) if not call.truncated else call.result)
        print(f"\n[{call.id}] {call.name} error={call.is_error} {call.duration_ms} ms "
              f"{len(call.result)} chars truncated={call.truncated}", file=sys.stderr)  # fmt: skip
        return 1 if call.is_error else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
