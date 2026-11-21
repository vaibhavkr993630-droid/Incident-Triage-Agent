# Redis unavailable (cache and job queue)

Owner: platform team. Applies to: redis, shop-api, worker.

## When to use
shop-api logs "cache read failed" or "failed to enqueue fulfillment job", its /health
reports redis failing, or the worker logs "cannot read fulfillment queue".

## How to confirm
- cache_requests_total with result error, queue_publish_errors_total, and
  worker_redis_errors_total all rising together point to redis itself.
- shop-api's /health checks redis and postgres separately; redis failing while postgres is
  ok isolates the problem.

## Impact
Reads fall back to postgres, so the shop stays up but slower and puts more load on the
database. Orders are still created, but their fulfillment jobs are lost while redis is down:
those orders stay in "paid" status and need to be re-queued after recovery.

## Mitigation
Restore redis. Then re-enqueue fulfillment for orders stuck in "paid" status that were
created during the outage.
