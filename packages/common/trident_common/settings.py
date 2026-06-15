"""Centralised configuration. Every service reads from the same env contract."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- AIS feed --------------------------------------------------------
    aisstream_api_key: str = ""
    ais_source: str = "synthetic"   # "live" | "synthetic" — synthetic needs no key
    # When True, subscribe to the WHOLE WORLD (all ships everywhere) rather than
    # the six chokepoint boxes. Heavy detection + forensic track persistence stay
    # scoped to the chokepoint zones; the global feed drives live worldwide display.
    ais_global: bool = False

    # --- datastores ------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql://trident:trident@localhost:5432/trident"

    # --- cognition (Groq) ------------------------------------------------
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    desk_search_enabled: bool = False        # web search tool for the Desk agent
    tavily_api_key: str = ""                  # optional, powers Desk web search

    # --- fusion layer (structured now, used later) -----------------------
    copernicus_user: str = ""                 # SAR (Sentinel-1) catalogue
    copernicus_password: str = ""
    fred_api_key: str = ""                    # market backbone
    eia_api_key: str = ""
    fusion_enabled: bool = False              # master switch for adapters

    # --- service wiring --------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"

    # severity threshold τ for the human-review gate / auto-confirm
    escalation_tau: float = 0.6


@lru_cache
def get_settings() -> Settings:
    return Settings()
