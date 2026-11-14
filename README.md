# Incident Triage Agent

An agentic incident-triage system. When a service misbehaves, an LLM agent investigates it the way an
on-call engineer would: it queries logs and metrics, checks recent deploys and config changes, and
searches runbooks. It then reports the most likely root cause, with every claim tied to the evidence
it came from.

The project includes the system being investigated. A small multi-service "lab" (an API, Postgres,
Redis, a background worker and a mock payment provider) runs under steady traffic, and a fault
injector breaks it in known ways. Because every injected fault has a known true cause, the agent's
accuracy is measured, not guessed.

> **Status:** in development. This README grows as each part is built.

## Planned architecture

```
loadgen ─► lab services ─► logs (Loki) + metrics (Prometheus) + change log
                ▲                          │
   fault injector (known faults)           │ read-only tools
                │                          ▼
          eval harness ◄──── triage agent ─► root-cause report + trace
```

| Part | Role |
|---|---|
| Lab | The target system that fails under injected faults |
| Telemetry | Prometheus for metrics, Loki for logs, a table of recent changes |
| Faults | Scenario files: how to break the lab, how to recover, and the true cause |
| Triage agent | Tool-calling loop with read-only tools, step and token budgets, structured report |
| Evals | Runs every scenario, scores the agent against the true cause, compares variants |

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## License

MIT. See [LICENSE](LICENSE).
