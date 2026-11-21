# Database connection pool exhaustion

Owner: platform team. Applies to: shop-api.

## When to use
shop-api returns 503, logs "database connection pool exhausted", or its /health reports a
postgres error.

## How to confirm
- db_pool_checked_out equal to db_pool_size (5) for a sustained period means every pooled
  connection is in use; db_pool_timeouts_total rising means requests wait and give up.
- A postgres error in shop-api's /health during pool exhaustion is usually a pool timeout,
  not a database outage: the health check could not get a connection from the pool.
- Requests that do not need a new connection (for example product reads served from the
  cache) keep working, which also suggests the pool, not the database, is the limit.

## Common causes
A code path that takes connections and never returns them (a leak), long transactions that
hold connections while waiting on something else, or traffic beyond the pool's capacity.

## Mitigation
Restart shop-api to release leaked connections as a stopgap, then find the leaking path.
Do not simply raise the pool size: a leak will drain a bigger pool too.
