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

## The lab

The lab is the system the agent investigates. It runs with Docker Compose:

```bash
docker compose up -d --build --wait
```

| Service | Role | Port |
|---|---|---|
| `shop-api` | FastAPI shop: products (cached in Redis), orders (charged through the payment provider, written to Postgres, queued for fulfillment) | 8000 |
| `payment-provider` | Mock third-party payment API | 8001 |
| `worker` | Takes fulfillment jobs off the Redis queue and marks orders fulfilled; restocks inventory | 9100 (metrics) |
| `loadgen` | Steady traffic, about 5 requests/second (mostly reads, some orders) | - |
| `postgres`, `redis` | Database and cache/queue | internal |

Every service writes one JSON object per log line (`ts`, `level`, `service`, `logger`, `msg`,
plus structured fields) and exposes Prometheus metrics: request rate and latency by route
template, database query latency and pool usage, cache hits and errors, payment outcomes and
latency, and worker job outcomes and queue depth. These logs and metrics are what the agent's
tools will read.

A dependency failure degrades the system in a visible way instead of crashing it. For example,
with Redis stopped, reads fall back to Postgres with a logged warning, fulfillment jobs fail to
enqueue with a logged error, and `/health` returns 503 naming Redis.

The whole stack uses about 310 MB of RAM.

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest                    # unit tests
uv run pytest -m integration     # against the running lab stack
```

## License

MIT. See [LICENSE](LICENSE).
