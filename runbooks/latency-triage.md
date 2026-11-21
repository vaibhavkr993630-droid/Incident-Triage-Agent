# High latency (general)

Owner: shop team.

## When to use
A latency alert fired, or users report slowness, and no runbook fits better yet.

## How to triage
- Break latency down by route: histogram_quantile over http_request_duration_seconds_bucket
  by route. One slow route points to its specific dependency; all routes slow points to
  something shared (database, CPU, network).
- For the slow route, check what it calls: postgres query durations, payment provider
  latency, cache errors (cache misses fall back to the slower database).
