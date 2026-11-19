"""Fault injection for the lab: scenario definitions, the injector, and signal checks.

This package is the only place that knows the true cause of an incident. The triage agent
must never import it or read the scenario files (that would be reading the answer key).
"""
