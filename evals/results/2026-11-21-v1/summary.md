Evaluation `2026-11-21-v1`: commit `01a18ff+uncommitted`, model `gemini-3.5-flash-lite`, 1 repeat(s), started 2026-11-21T18:52:49+00:00.

| Variant | Runs | Correct (category + component) | Category only | False alarms | No report | Provider errors | Quoted numbers: grounded / mis-cited / unsupported | Avg tool calls | Avg tokens | Avg seconds | Confidence when right / wrong |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `agent` | 10 | 100% | 100% | 0/1 | 0 | 0 | 44 / 3 / 0 of 47 (94%) | 8 | 31471 | 18 | 1.00 / - |
| `agent_no_runbooks` | 10 | 100% | 100% | 0/1 | 0 | 0 | 28 / 0 / 0 of 28 (100%) | 8 | 35813 | 25 | 0.99 / - |
| `baseline` | 10 | 90% | 100% | 0/1 | 0 | 0 | 27 / 1 / 0 of 28 (96%) | 12 | 3555 | 5 | 1.00 / 1.00 |

| Scenario | `agent` | `agent_no_runbooks` | `baseline` |
|---|---|---|---|
| `bad_payment_timeout_config` | 1/1 | 1/1 | 1/1 |
| `bad_release` | 1/1 | 1/1 | 1/1 |
| `connection_leak` | 1/1 | 1/1 | 0/1 (db_pool_exhausted@postgres) |
| `missing_index` | 1/1 | 1/1 | 1/1 |
| `no_fault` | 1/1 | 1/1 | 1/1 |
| `payment_errors` | 1/1 | 1/1 | 1/1 |
| `payment_slow` | 1/1 | 1/1 | 1/1 |
| `payment_slow_with_decoy` | 1/1 | 1/1 | 1/1 |
| `redis_down` | 1/1 | 1/1 | 1/1 |
| `worker_backlog` | 1/1 | 1/1 | 1/1 |
