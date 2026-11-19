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
| `shop-api` | FastAPI shop: products (cached in Redis), 24-hour product stats over 2M rows of order history, orders (charged through the payment provider, written to Postgres, queued for fulfillment), and a change log of deploys and config changes | 8000 |
| `payment-provider` | Mock third-party payment API | 8001 |
| `worker` | Takes fulfillment jobs off the Redis queue and marks orders fulfilled; restocks inventory | 9100 (metrics) |
| `loadgen` | Steady traffic, about 5 requests/second (product pages, product list, product stats, orders) | - |
| `postgres`, `redis` | Database and cache/queue | internal |
| `prometheus`, `loki` | Metrics and logs (see [Telemetry](#telemetry)) | 9090, 3100 |

Every service writes one JSON object per log line (`ts`, `level`, `service`, `logger`, `msg`,
plus structured fields) and exposes Prometheus metrics: request rate and latency by route
template, database query latency and pool usage, cache hits and errors, payment outcomes and
latency, and worker job outcomes and queue depth. These logs and metrics are what the agent's
tools will read.

A dependency failure degrades the system in a visible way instead of crashing it. For example,
with Redis stopped, reads fall back to Postgres with a logged warning, fulfillment jobs fail to
enqueue with a logged error, and `/health` returns 503 naming Redis.

## Telemetry

The agent will never read container output directly. It queries the same two systems an
on-call engineer would:

| System | Holds | Example query |
|---|---|---|
| Prometheus (`:9090`) | Metrics scraped every 5 s from `shop-api`, `payment-provider` and `worker`. The Prometheus `job` label is the service name. | `sum by (route) (rate(http_requests_total{job="shop-api"}[1m]))` |
| Loki (`:3100`) | Every service's JSON log lines, labelled only by `service` and `level`. Other fields are filtered at query time. | `{service="shop-api"} \| json \| route="/orders" \| status="500"` |

Logs are pushed to Loki from inside each service by a small batching handler
(`lab/common/loki.py`), rather than by a separate log-collector container. That saves memory
on a laptop and behaves the same in CI. The handler never blocks or raises: if Loki is down,
requests are unaffected, and lost lines are counted in `log_shipping_dropped_total`. A
production setup would usually run a collector such as Grafana Alloy instead.

The whole stack, telemetry included, uses about 500 MB of RAM. Published ports bind to
`127.0.0.1` only, because the lab's control routes are unauthenticated.

## Fault injection

Every fault is a scenario file in [`faults/scenarios/`](faults/scenarios/). A scenario says how
to break the lab, how to undo it, what the true cause is, what the on-call engineer is told
(the alert), and which signals in Prometheus and Loki prove the fault is really observable.

| Scenario | True cause (category, component) | How it is injected |
|---|---|---|
| `missing_index` | `db_slow_query`, postgres | Drop the index the product-stats query relies on (2M rows of order history) |
| `connection_leak` | `db_pool_exhausted`, shop-api | A code path checks out every pooled connection and never returns them |
| `redis_down` | `cache_unavailable`, redis | Stop the Redis container |
| `bad_payment_timeout_config` | `bad_config_change`, shop-api | Config change sets the payment timeout to 30 ms, recorded in the change log |
| `payment_slow` | `dependency_slow`, payment-provider | Provider adds 3 s of latency; checkout calls hit the 2 s timeout |
| `payment_errors` | `dependency_errors`, payment-provider | Provider fails 80% of charges with HTTP 500 |
| `worker_backlog` | `worker_backlog`, worker | Each fulfillment job takes 8 s; the queue backs up |
| `bad_release` | `bad_release`, shop-api | A recorded release breaks checkout for two products (`KeyError` traceback) |
| `payment_slow_with_decoy` | `dependency_slow`, payment-provider | Slow provider hidden behind loud, unrelated warnings and an unrelated recent change |
| `no_fault` | `no_fault`, none | Nothing; a vague complaint with healthy telemetry (tests false alarms) |

Five of these scenarios raise the same alert, "Checkout errors: more than 5% of POST /orders
requests have failed with 5xx". The alert alone cannot tell them apart; only investigation can.
Alerts describe symptoms and never name the cause, which a unit test enforces.

Each service has a small, typed set of fault flags behind internal `/_faults` routes. These
control-plane routes are never logged or counted in metrics, so the injection itself leaves no
trace for the agent to read. A live test checks this after running every scenario.

```bash
uv run python -m faults list                 # scenarios and their true causes
uv run python -m faults run missing_index    # inject, verify signals, recover
uv run python -m faults run --all
uv run python -m faults baseline             # is the lab healthy right now?
uv run python -m faults recover-all          # undo everything after an interrupted run
```

`run` waits for a healthy baseline, injects, waits for the fault to show up, checks every
signal, then always recovers and confirms the baseline again.

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest                    # unit tests
uv run pytest -m integration     # against the running lab stack
uv run pytest -m scenario        # inject every fault into the running lab (about 10 minutes)
```

## License

MIT. See [LICENSE](LICENSE).
