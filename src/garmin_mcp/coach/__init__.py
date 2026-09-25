"""Adaptive training coach built on top of the Garmin Connect client.

The coach builds a periodised half-marathon plan, adapts it to readiness and
training load, rates every finished session against the plan and pushes the
result to the phone via ntfy. ``garmin-coach sync`` is the entry point used by
the scheduled GitHub Actions workflow.
"""
