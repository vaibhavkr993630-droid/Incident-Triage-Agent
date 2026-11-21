# Vague reports and possible false alarms

Owner: on-call.

## When to use
A customer complaint or a vague report with no alert, or an alert that may be noise.

## How to decide
Check health, error-level logs, error rates, latency by route and queue depth over the
relevant window. If all are normal, report that nothing is wrong now, with the evidence,
rather than inventing a cause. Note the time window you checked: a past problem may have
ended already.
