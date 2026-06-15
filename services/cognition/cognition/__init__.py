"""TRIDENT cognition service — the Tier-3 LangGraph agent swarm.

A single always-resident process that listens on the Redis signal stream and
drives a durable Sentinel -> Analyst -> Desk state machine for every Signal.
The graph is checkpointed in Postgres so an incident under investigation
survives a restart.
"""

__version__ = "1.0.0"
