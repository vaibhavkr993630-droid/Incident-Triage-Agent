# Checkout failures (POST /orders returning 5xx)

Owner: shop team. Applies to: shop-api.

## When to use
The checkout error alert fired, or customers report that placing an order fails. An order
touches four things in sequence: product lookup (redis, then postgres), the payment charge
(payment-provider), the order write (postgres), and the fulfillment job (redis queue). A
failure in any of them can surface as a 5xx on POST /orders.

## Read the status code first
- 504: the payment charge timed out. Go to "Payment timeouts" below.
- 502: the payment provider answered with an error, or could not be reached.
- 503: shop-api could not get a database connection. See the database connection pool runbook.
- 500: an unhandled exception inside shop-api. Look for an exception traceback in the
  shop-api error logs and check the change log for a recent shop-api deploy.

## Payment timeouts
Compare two numbers: the timeout shop-api used (the timeout_s field on "payment provider
timed out" log lines) and the payment provider's own /charge latency (its
http_request_duration_seconds histogram). If the provider is slow, the provider is the
problem. If the provider is fast but shop-api gives up before it answers, shop-api's
timeout setting is wrong; check the change log for a recent config change.

## Mitigation
Never retry payments blindly: a retried charge can bill a customer twice. Roll back a bad
shop-api change, or escalate a provider problem to the provider.
