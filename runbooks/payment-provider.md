# Payment provider degradation

Owner: payments team. Applies to: payment-provider (external dependency of shop-api).

## When to use
shop-api reports "payment provider timed out", "payment provider returned an error" or
"payment provider unreachable", or payment_requests_total shows outcomes other than approved.

## How to confirm
- Latency: histogram_quantile over the provider's http_request_duration_seconds_bucket for
  route /charge. Healthy is under 100 ms.
- Errors: the provider's http_requests_total with status 500 for /charge, and its own error
  logs (for example "card processor unavailable").
- Reachability: service_health for payment-provider.
- Compare with shop-api's side: the provider's own metrics tell you whether the provider is
  slow, or whether shop-api is giving up too early.

## Common causes
Upstream card-processor outage, provider overload, a provider-side deploy, network problems
between shop-api and the provider.

## Mitigation
Escalate to the provider with the time window, error rate and latency figures. Consider
pausing checkout with a clear message rather than letting requests pile up.
