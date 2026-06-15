"""TRIDENT api gateway (Tier 4).

A single async FastAPI app that serves the command center: REST snapshots of
vessel/incident/zone state plus one multiplexed WebSocket (`/ws`) that fans
`vessel_delta`, `signal_tick`, `incident` and `zone_stats` frames to the UI.

It is a pure *reader* of the spine: vessel hot-state from Redis, the durable
record from Postgres, and the live bus (`keys.STREAM_SIGNALS`/`STREAM_INCIDENTS`)
via consumer-group reads. It never imports another service's code.
"""

__version__ = "1.0.0"
