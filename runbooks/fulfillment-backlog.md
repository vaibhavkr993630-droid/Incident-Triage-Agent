# Fulfillment backlog

Owner: fulfillment team. Applies to: worker.

## When to use
Orders stay in "paid" status for a long time, or worker_queue_depth keeps growing.

## How to confirm
- Compare intake with processing: orders_total with outcome created (orders coming in)
  against worker_jobs_total with outcome fulfilled (orders processed). A growing gap with
  normal intake means the worker is too slow.
- worker_job_duration_seconds and "fulfillment job slow" warnings show how long each job
  takes. Healthy jobs take milliseconds.
- If the worker also logs "cannot read fulfillment queue", the problem is redis, not the
  worker: see the redis runbook.

## Common causes
A slow downstream call inside each job, a worker deploy, a stuck worker process.

## Mitigation
Find what makes each job slow. Adding workers only helps if the slowness is not shared.
