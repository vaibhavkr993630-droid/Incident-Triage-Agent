# Deploys and configuration changes during an incident

Owner: all teams.

## When to use
Any incident. Check the change log early: most incidents follow a change.

## Judging a change
A change shortly before the problem began is a suspect, not proof. Confirm the mechanism:
does what the change did explain the exact symptoms? A config change to a timeout explains
timeouts; a logging change does not explain payment failures. A release that adds a
feature explains new exceptions in the code it touched.

## Rolling back
Prefer a rollback over a forward fix while customers are affected. Record the rollback in
the change log. If symptoms persist after the rollback, the change was not the cause.
