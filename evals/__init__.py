"""Scored evaluation: inject each fault, run each variant, score against the true cause.

This package may read the answer key (faults/) because it is the examiner, not the agent.
The agent itself (triage/) never imports it.
"""
