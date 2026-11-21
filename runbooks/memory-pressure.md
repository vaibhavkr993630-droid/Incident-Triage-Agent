# Memory pressure and OOM kills

Owner: platform team.

## When to use
Containers restart unexpectedly, the kernel reports OOM kills, or a process's memory grows
until it is killed.

## How to confirm
Check container restart counts and exit code 137, and process memory over time.

## Mitigation
Raise the memory limit as a stopgap, then find the leak or the oversized workload.
