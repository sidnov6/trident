# TRIDENT — Data Fusion Layer

AIS alone has a blind spot that is almost poetic: the vessels you most want to watch are the
ones that turn AIS **off**. This layer fuses AIS with sensors and datasets that see what AIS
can't, turning *"this vessel is acting strange"* into *"this is a named, sanctioned, dark
tanker — caught on radar — and here is the freight shock."*

The whole layer is **structured now, wired incrementally**. The seams already exist in code:

- **DB join targets** — `sanctions_vessels`, `sar_scenes` tables (`services/db/schema.sql`).
- **Adapter seam** — `services/cognition/cognition/fusion/` implements a `FusionAdapter`
  protocol (`enrich(vessel, signal) -> dict | None`). The Analyst calls every registered
  adapter and folds results into `AnalystOutput.{sanctions_match, sar_confirmation,
  weather_context, osint_context}`. Master switch: `FUSION_ENABLED` in `.env`.
- **Contract fields** — `AnalystOutput` already carries the fusion fields, and the TS mirror
  exposes them to the UI, so a hit renders the moment an adapter goes live.

## Everything joins to AIS on one of three keys

| Key | Source field | Why it matters |
|---|---|---|
| **MMSI** | live radio identity in every AIS message | the join for live-feed sources |
| **IMO** | permanent hull identity | survives renaming/reflagging — exactly how shadow fleets hide |
| **space-time** | lat / lon / timestamp | the only key for sources that don't know the ship's identity (a satellite) |

That third key is the important one: it's how you fuse in the vessels AIS can't see.

---

## The layers, in order of impact

### 1. SAR satellite imagery — dark-vessel ground truth · **space-time** · free
Sentinel-1 radar sees through cloud and darkness and detects vessels transmitting no AIS at
all. **Fusion:** when `dark_vessel.py` fires (AIS gap in the core), query the Copernicus
catalogue for a Sentinel-1 scene over that lat/lon/time. A radar blip where AIS says
"nothing here" is a confirmed dark vessel *with a picture*. Sentinel-1C/D carry AIS antennas,
enabling automatic correlation. SAR revisit is hours-to-days → treat as **forensic
confirmation**, not a real-time feed.
- **Seam:** `fusion/sar.py` → writes a `sar_scenes` row (footprint polygon, `matched_incident`).
- **Integration note:** this is the trickiest one — async, scene-search, not a stream. Use the
  Copernicus Data Space OData/STAC catalogue: filter `Collection=SENTINEL-1`, an
  `OData.CSC.Intersects` footprint around the gap coordinate, and an acquisition-time window
  bracketing the blackout. Returns scene IDs + download links; store the footprint in PostGIS
  and check `ST_Contains(footprint, gap_point)`.

### 2. Sanctions & ownership — anomaly → named threat · **MMSI / IMO** · free · *wired live*
OFAC's SDN list contains sanctioned vessels with IMO, MMSI, flag, former flag, aliases, type,
tonnage and owning entity. OpenSanctions aggregates OFAC + EU + UN + UK for Frankfurt/EU
relevance. **Fusion:** cross-reference every vessel entering a chokepoint; a match instantly
elevates the case.
- **Seam:** `fusion/ofac.py` is **already live** — it queries the `sanctions_vessels` table by
  MMSI/IMO (cheap, no external call) and populates `AnalystOutput.sanctions_match`. Empty table
  → returns `None`. **To activate:** load the OFAC SDN advanced file (or an OpenSanctions
  export) into `sanctions_vessels` (a loader script is the only missing piece; the join is done).

### 3. Pre-computed AIS event intelligence — Global Fishing Watch · **MMSI** · free (registration)
GFW's API gives AIS-derived events already computed at global scale: encounters (ship-to-ship),
loitering, AIS gaps, port visits. Use as a **validation oracle** for our own detectors and a
historical-pattern source for the Analyst.
- **Seam:** a future `fusion/gfw.py` adapter; cross-checks our Signal against GFW's event for
  the same MMSI/time → raises confidence when they agree.

### 4. OSINT events — GDELT · **space-time** · free · the "why" layer
When congestion spikes or vessels reroute, GDELT tells you whether it's a blockage, a closure,
or a conflict. **Fusion:** a `CONGESTION` signal at Hormuz + a GDELT military-incident event
geolocated nearby = an *explained, high-confidence* incident instead of a mystery.
- **Seam:** `fusion/osint.py` (future) → populates `AnalystOutput.osint_context`. GDELT Cloud
  exposes Events/Stories/Entities via REST + MCP tools that plug into the LangGraph cognition
  layer natively.

### 5. Marine weather — Open-Meteo · **space-time** · free, no key · disambiguates intent
A tanker loitering could be a sanctions rendezvous or just waiting out a storm. **Fusion:**
pull wave height / current / sea state at the loitering vessel's position; downgrade
weather-driven holds, upgrade loitering with no environmental excuse. The difference between a
noisy alert stream and a credible one.
- **Seam:** `fusion/weather.py` (stub) → Open-Meteo Marine API at the vessel position →
  `AnalystOutput.weather_context`. Gated on `FUSION_ENABLED`.

### 6. Market data — the Desk agent's fuel · the Frankfurt differentiator
FRED (Brent `DCOILBRENTEU`, Henry Hub, Global Supply Chain Pressure Index) + EIA energy prices
are free backbones; Baltic Exchange (VLCC/Suezmax/Aframax/LNG) is the paid authoritative
benchmark. **Fusion:** feed these to the Desk agent so a note reads "Suez transit risk ↑ →
Brent +X, Cape reroute +12 days" with live numbers attached.
- **Seam:** the Desk agent's optional web-search tool (`DESK_SEARCH_ENABLED` + Tavily) already
  separates retrieved facts from inference; a `fusion/market.py` FRED/EIA client drops in to
  give it structured live rates instead of search.

### 7. Reference geodata — static, load once
Marine Regions (EEZ polygons), NGA World Port Index, GEBCO bathymetry, Natural Earth coastlines
— make geofencing precise against real legal boundaries, not hand-drawn boxes. Loads into
PostGIS alongside the Suez zones in `packages/geo/trident_geo/zones/`.

---

## The killer fusions (what makes a bureau lean in)

- **Confirmed dark vessel** = AIS gap (ingestor) + radar contact at the gap coordinates
  (Sentinel-1) → a vessel that went dark, caught on camera.
- **STS sanctions evasion** = two vessels go dark in the same place/time (ingestor STS pair) +
  a SAR scene showing two hulls rafted together (Sentinel-1) + ≥1 IMO on the SDN list (OFAC) →
  the full pattern, **named and pictured**.
- **Explained vs. unexplained crisis** = congestion z-spike (ingestor) cross-checked against
  weather (Open-Meteo) and news (GDELT) → instantly separates an Ever-Given grounding from a
  military closure from a passing storm.

## Recommended activation order

1. **OFAC** — already wired; just load the SDN file into `sanctions_vessels`.
2. **Open-Meteo** — free, no key, immediately sharpens loitering precision.
3. **SAR (Sentinel-1)** — highest-value, but the async catalogue integration is the real work.

Each demonstrates a different join key (MMSI/IMO, space-time, space-time), which is the point.
