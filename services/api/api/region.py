"""Regional traffic analysis — ship-type composition + an on-demand LLM narrative.

Two-speed, applied to regions:
  * Deterministic ship-type COUNTS for a viewport are cheap (a GEO read + a
    histogram) and always available — no LLM. The UI shows these live.
  * The narrative DEEP-DIVE is the only LLM call, made on demand when the analyst
    clicks "analyze". A direct Groq chat call (no heavy deps) keeps the agent
    24/7-cheap: it runs only when asked.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from trident_common.settings import get_settings
from trident_contracts.enums import ShipTypeBucket, bucket_for_ship_type

log = logging.getLogger("api.region")

_BUCKET_LABEL = {
    ShipTypeBucket.TANKER: "tanker",
    ShipTypeBucket.CARGO: "cargo",
    ShipTypeBucket.PASSENGER: "passenger",
    ShipTypeBucket.FISHING: "fishing",
    ShipTypeBucket.HIGH_SPEED: "high-speed",
    ShipTypeBucket.TUG_SPECIAL: "tug/special",
    ShipTypeBucket.OTHER: "other",
}

PROMPT_VERSION = "region-analyst-v1"


def ship_type_counts(states: list[Any]) -> dict[str, int]:
    """Histogram of coarse ship-type buckets over a set of VesselState."""
    counts: dict[str, int] = {label: 0 for label in _BUCKET_LABEL.values()}
    for s in states:
        bucket = bucket_for_ship_type(getattr(s, "ship_type", None))
        counts[_BUCKET_LABEL.get(bucket, "other")] += 1
    return counts


def _region_name(bbox: tuple[float, float, float, float]) -> str:
    min_lat, min_lon, max_lat, max_lon = bbox
    ns = "N" if (min_lat + max_lat) / 2 >= 0 else "S"
    ew = "E" if (min_lon + max_lon) / 2 >= 0 else "W"
    return f"~{abs((min_lat+max_lat)/2):.1f}°{ns} {abs((min_lon+max_lon)/2):.1f}°{ew}"


async def analyze_region(
    bbox: tuple[float, float, float, float],
    counts: dict[str, int],
    total: int,
) -> dict[str, Any]:
    """Run the Regional Analyst narrative over a region's ship-type composition.

    Returns ``{analysis, model, prompt_version}``. Falls back to a deterministic
    template when no Groq key is configured (the agent still answers, just
    rule-based)."""
    settings = get_settings()
    breakdown = ", ".join(f"{k} {v}" for k, v in counts.items() if v) or "no vessels"
    region = _region_name(bbox)

    if not settings.groq_api_key:
        return {
            "analysis": _deterministic(region, counts, total),
            "model": "deterministic-fallback",
            "prompt_version": PROMPT_VERSION,
        }

    system = (
        "You are TRIDENT's Regional Maritime Analyst. Given a sea region and the "
        "live ship-type composition of vessels currently in it, describe in 3-4 "
        "crisp sentences what maritime activity is happening: which vessel types "
        "dominate, what that implies about the area's role (tanker-heavy = an oil/"
        "energy route; cargo/container-heavy = a trade lane; fishing-heavy = fishing "
        "grounds; passenger/high-speed = ferry corridors), and anything notable. "
        "Keep observation separate from inference. No preamble."
    )
    user = (
        f"Region {region}, bbox lat[{bbox[0]:.1f},{bbox[2]:.1f}] lon[{bbox[1]:.1f},"
        f"{bbox[3]:.1f}]. Live vessels in view: {total}. By type: {breakdown}. "
        "Describe the maritime picture."
    )
    try:
        import httpx

        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                json={
                    "model": settings.groq_model,
                    "max_tokens": 260,
                    "temperature": 0.4,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
        return {"analysis": text, "model": settings.groq_model, "prompt_version": PROMPT_VERSION}
    except Exception as exc:  # pragma: no cover - network/LLM guard
        log.warning("region analyze LLM failed (%s); using fallback", exc)
        return {
            "analysis": _deterministic(region, counts, total),
            "model": "deterministic-fallback",
            "prompt_version": PROMPT_VERSION,
        }


def _deterministic(region: str, counts: dict[str, int], total: int) -> str:
    if total == 0:
        return f"No live vessels currently in view around {region}."
    ranked = sorted(((v, k) for k, v in counts.items() if v), reverse=True)
    top = ranked[0][1] if ranked else "other"
    lead = {
        "tanker": "tanker-heavy — consistent with an oil/energy shipping route",
        "cargo": "cargo-dominated — a commercial trade lane",
        "fishing": "fishing-heavy — active fishing grounds",
        "passenger": "passenger-heavy — a ferry / cruise corridor",
        "high-speed": "high-speed-craft heavy — a fast-ferry corridor",
        "tug/special": "service/tug traffic — likely a port approach or works area",
    }.get(top, "mixed traffic")
    parts = ", ".join(f"{v} {k}" for v, k in ((v, k) for k, v in counts.items() if v))
    return (
        f"{total} vessels in view around {region}; the mix is {lead}. "
        f"Composition: {parts}."
    )
