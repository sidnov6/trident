"""TRIDENT replay service (Tier 4) — the forensic time machine.

Given an MMSI + time window, streams the historical ``tracks`` back at adjustable
speed so an analyst scrubs a past event on the same map UI, and answers the
chain-of-custody question — "who was within N nm of the dark vessel during its
blackout?" — with a PostGIS ``ST_DWithin`` proximity query over the hypertable.

A pure reader of Postgres; it imports no other service's code.
"""

__version__ = "1.0.0"
