"""TRIDENT ingestor service — Tier 0-2.

Owns AIS ingest (one persistent WebSocket or a synthetic generator), the
latest-state-wins Redis world model, the deterministic detector suite, an async
track writer, and Signal publication onto the event bus. No LLM anywhere on the
per-message path.
"""

__version__ = "1.0.0"
