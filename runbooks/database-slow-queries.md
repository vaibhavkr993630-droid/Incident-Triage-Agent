# Slow database queries

Owner: platform team. Applies to: postgres, shop-api.

## When to use
Latency is high on routes that read from postgres, shop-api logs "slow database query"
warnings, or db_query_duration_seconds rises for one query name.

## How to confirm
- Find which query is slow: db_query_duration_seconds by query label, and the query field
  of the slow-query warnings. One slow query points to that query's plan; every query slow
  at once points to the database server itself.
- Check whether only the routes that use that query are slow. Error rates are usually
  normal: slow queries delay requests, they do not fail them, until timeouts start.

## Common causes
A missing or dropped index (a query that used an index now scans the whole table), a large
data growth, a schema migration, lock contention from a long transaction.

## Mitigation
Run EXPLAIN on the slow query to see whether it uses an index. Recreate a missing index
(CREATE INDEX CONCURRENTLY avoids blocking writes). Check recent migrations.
