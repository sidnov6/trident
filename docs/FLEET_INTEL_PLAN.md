All verified. The API WS uses a `StreamFanout` with `_ensure_group` + `_tail_signals`/`_tail_incidents` shared XREADGROUP loops and a `_broadcast` fan-out — exactly the pattern a third `_tail_alerts` consumer slots into. `persistence.py` has `write_incident` + `publish_incident` where auto-flag SADD hooks in. `writer.py` has `enqueue_fix(state)` and a bounded `_tracks` deque; `_INSERT_TRACK` takes `(ts,mmsi,geom,sog,cog,heading,nav_status,zone)` — so a `enqueue_fix_raw` must supply NULLs for the missing kinematic columns. Config thresholds confirmed.

I now have everything grounded. Here is the synthesis.

---

# TRIDENT — Real-Time Fleet Threat Intelligence: Final Implementation Plan

This reconciles the five memos against the actual code. Where memos conflicted or were wrong about the code, I corrected to ground truth (noted inline). The plan adds one new service, one new Redis stream, one new contract, watchlist-driven persistence, and a layperson UI — without touching the ingest hot path, the 33ms render loop, or the existing LangGraph pipeline.

**Code corrections baked in (memos vs. reality):**
- `haversine_nm`/`implied_speed_kn` live in `services/ingestor/ingestor/geoutil.py`, NOT `trident_geo`. The new `fleetscan` service must vendor/import these (copy to a shared spot or duplicate the ~20 lines — they have no deps).
- `point_in_geojson` / `near_edge` are NOT in `trident_geo` top-level; `near_edge` is in `chokepoints.py` and is zone-specific. Global agents must not depend on them.
- The render loop already derives `selectedMmsi` from the selected **incident** (`Map.tsx:240`). We extend that one line, not add a parallel highlight system.
- `--amber` is navy `#1f5fbf` (already the "selected" color `[31,95,191]`). Reuse it; don't introduce a new accent.

---

## 1. FINAL THREAT TAXONOMY

Eight categories. The taxonomy memo's 10 collapsed: FLAG_RISK is demoted to a **risk multiplier** (FOC alone is legal/common — not a standalone alert), and SANCTIONS_RISK is the behavioral proxy (the real OFAC list in `fusion/ofac.py` is empty, so a true hit is cognition-tier only). Each row is what `fleetscan` actually emits.

| # | Category (enum) | Layperson label | Color | Detection logic (from fields we have) | Scope | Honesty |
|---|---|---|---|---|---|---|
| 1 | `GONE_DARK` | "Went dark" | `#000000` | `now − last_fix_ts > gap` while last `sog > 0.5`; reappearance = jump after gap (port of `dark_vessel.py`, global cadence) | Global | Fully deterministic |
| 2 | `DARK_FLEET` | "Shadow tanker" | `#B5179E` magenta | tanker bucket (or `draught≥8` + `imo`) AND `is_flag_of_convenience(flag)` AND (recent GONE_DARK OR dest missing/`"---"` OR `nav_status==15` while moving) | Global | Strong heuristic profile, not identity |
| 3 | `SPOOFING` | "Faking position" | `#7209B7` violet | `IDENTITY_CONFLICT` (same MMSI, same ts, ≥0.5nm) OR `POSITION_JUMP` (implied speed >40kn) — port of `spoofing.py` | Global | Fully deterministic |
| 4 | `LOITERING` | "Hanging around" | `#FFB703` amber | `sog<0.5` over N consecutive sweeps, outside anchorage, not fishing bucket; suppress `nav_status∈{1,5}` | Zone (needs history) | Deterministic; anchorage mask Suez-only |
| 5 | `STS_TRANSFER` | "Meeting at sea" | `#F48C06` orange | loitering tanker + `GEOSEARCH GLOBAL_GEO 0.5nm` finds another slow tanker | Global (uses `vessels:geo`) | Deterministic; needs proximity query |
| 6 | `SANCTIONS_RISK` | "Possible sanctions evasion" | `#D00000` red | DARK_FLEET profile + GONE_DARK reappearance `disp>5nm` co-occur ≤1h. Confidence capped **0.55** (behavior, not OFAC list) | Global | Proxy; real hit is cognition-tier |
| 7 | `NAV_HAZARD` | "Blocking / aground" | `#FF006E` hot pink | `nav_status==6` (aground) global, OR canal-strip dwell (`geofence.py`) | Zone+global | `nav_status==6` global fallback |
| 8 | `GREY_ZONE` | "Possible military" | `#2D6A4F` green | `name` token (`NAVY/WARSHIP/COAST GUARD/PATROL`) OR military `ship_type`; ↑ in contested chokepoint | Global | Inferential; confidence ≤0.6, "possible" |

**FLAG_RISK** (FOC) is not its own alert — it is surfaced as a **badge** in the dossier and a **×1.15 multiplier** on the composite score. True "flag hopping" is **not detectable** (flag is a deterministic function of a fixed MMSI MID; the ring buffer holds only `(ts,lat,lon)`) — we never claim it.

**Composite risk score** (deterministic, per vessel per sweep), drives ticker sort + map band color:
```
base  = max over fired categories of (W_k · severity_k · confidence_k)   # dominant threat, not a sum
boost = 0.15 · (count of OTHER fired categories scoring > 0.3)            # corroboration
risk  = clamp((base + boost) · (1 + 0.15·is_FOC), 0, 1)
W = {SANCTIONS_RISK:1.0, NAV_HAZARD:.95, DARK_FLEET:.90, GONE_DARK:.85, SPOOFING:.85, STS_TRANSFER:.80, GREY_ZONE:.70, LOITERING:.55}
```
**Severity bands → one color a layperson reads:** `≥.80` CRITICAL red · `.60–.79` HIGH orange · `.35–.59` WATCH amber · `<.35` CLEAR green (not shown). Map dot = dominant category color; ticker chip border = band color.

---

## 2. ARCHITECTURE

### 2a. New service `services/fleetscan/` (the always-on deterministic scanner)
A standalone asyncio service mirroring the ingestor's structure (`get_settings()`, optional Redis, graceful shutdown). **Decision (from agents memo, confirmed against code):** NOT bolted onto the ingestor — its hot path (`_ingest_loop`) is per-message and the detectors are stateful in-process objects scoped to zone traffic. A whole-world per-message classification would reintroduce exactly the cost the `if not in_zone: continue` (`main.py`) avoids. Instead fleetscan is a **slow snapshot sweep over Redis world-state**.

**The sweep** (every `SCAN_INTERVAL_S=10s`):
```
mmsis = await redis.zrange(GLOBAL_GEO, 0, -1)        # 1 call, every live vessel
for chunk in batched(mmsis, 500):
    pipe = redis.pipeline(); [pipe.hgetall(vessel_key(m)) for m in chunk]; states = await pipe.execute()
    for hash in states:
        st = state_from_hash(hash)                    # reuse api/state_reader.state_from_hash
        for agent in AGENTS: alert = agent.classify(st, mem[mmsi], now)  # pure Python
        if any: dedupe -> XADD STREAM_FLEET_ALERTS; append fleet:track ring if flagged
```
Cost: ~2 Redis ops per 500 vessels every 10s. 5,000 vessels ≈ 20 ops + pure-Python rules / 10s — trivial on HF cpu-basic. **No DB reads in the sweep.** Cross-sweep memory (prev position, was-dark, loiter streak) is an in-process bounded `dict[mmsi→AgentMemory]`, pruned when MMSI drops out of `GLOBAL_GEO` — same shape as `dark_vessel.py:self._last`.

**Files:** `services/fleetscan/fleetscan/{main.py, sweep.py, memory.py, breadcrumbs.py, config.py, geoutil.py (copied), agents/{base,gone_dark,dark_fleet,spoofing,loitering,sts,sanctions,nav_hazard,grey_zone}.py}`. Each agent ≤60 lines, thresholds in `config.py`.

### 2b. The alert stream + contract
New Redis stream `STREAM_FLEET_ALERTS = "trident:fleet_alerts"` — **separate from `STREAM_SIGNALS`** (which feeds the zone-forensic LangGraph that assumes DB-backed dossiers global vessels lack; flooding it would firehose Groq). New consumer group `CONSUMER_GROUP_API_FLEET = "api_fleet"`. Add to `packages/common/trident_common/keys.py`:
```python
STREAM_FLEET_ALERTS = "trident:fleet_alerts"
CONSUMER_GROUP_API_FLEET = "api_fleet"
WATCHLIST_META = "watchlist:meta"          # HSET {mmsi -> json: category, reason, flagged_ts}
def fleet_track_key(m): return f"fleet:track:{m}"      # capped LIST "ts,lat,lon", TTL=VESSEL_TTL_S
def fleet_cooldown_key(m,c): return f"fleet:cd:{m}:{c}"
```

New contract `packages/contracts/trident_contracts/fleet_alert.py` (modeled on `Signal.to_stream_fields`/`from_stream_fields` so the API tailer reuses code):
```python
class ThreatCategory(str, Enum): GONE_DARK; DARK_FLEET; SPOOFING; LOITERING; STS_TRANSFER; SANCTIONS_RISK; NAV_HAZARD; GREY_ZONE
class FleetAlert(BaseModel):
    id; ts; category: ThreatCategory; agent: str; mmsi; name|None; flag|None
    ship_bucket: int; severity; confidence; risk: float; position: tuple[lat,lon]
    cog; sog; zone|None; evidence: dict[str,Any]   # plain-language strings
    narrative: str|None = None                     # LLM, top-N only
    detector_version: str
    def to_stream_fields(self)->{"payload": self.model_dump_json()}
class FleetAlertLite(...)  # id,ts,category,mmsi,name,severity,risk,position — the ticker wire
```
Denormalized `name/flag/cog/position` so the ticker renders with zero extra fetch (no per-frame lookup). Add `ThreatCategory` to `enums.py` + export from `__init__.py`. **Conflict resolved:** the notify memo called this `Alert`/`AlertCategory`; the canonical Python name is `FleetAlert`/`ThreatCategory`, and the TS mirror in `web/lib/contracts.ts` uses the same names.

### 2c. Selective + rate-limit-safe LLM layer
One `NarratorAgent` as a separate slow coroutine in fleetscan — **the only Groq caller in this plane**. None of the per-vessel agents touch Groq. It reuses `cognition.llm.structured`/`has_llm` (one shared `ChatGroq`, one key).
```
every NARRATE_INTERVAL_S=30s:
    cand = new alerts since last cycle with severity >= 0.7, dedup by mmsi
    for a in nlargest(3, cand, key=severity):
        if circuit_open: break
        try: a.narrative = await groq_narrate(a); republish a
        except RateLimit(429): open_circuit(30→60→120s); break
```
Hard cap ≤3 calls / 30s ≈ 6/min, well under Groq free tier, leaving headroom for the existing cognition graph sharing the key. On 429 → ship alerts with `narrative=None`; UI falls back to deterministic `evidence` strings. When `has_llm()` is False, NarratorAgent is a no-op. This is the same graceful-degrade contract the codebase already uses. The existing LangGraph pipeline is **untouched**; optionally a confirmed high-severity zone alert (`severity≥τ AND zone is not None`) may be promoted into `STREAM_SIGNALS`, gated so global vessels never enter it.

### 2d. Watchlist-driven track persistence (the "show its path" engine)
Reuse `WATCHLIST_PRIORITY` SET as the single "persist this MMSI" source. **Two producers SADD:** (1) `cognition/persistence.py:write_incident` auto-flags (`SADD watchlist:priority` + `HSET watchlist:meta`); (2) fleetscan SADDs on a high-risk alert; (3) API `POST /vessels/{mmsi}/flag` for manual flagging. **Critically, fleetscan also writes a `fleet:track:{mmsi}` breadcrumb ring (flagged vessels only, a few hundred max)** — this gives global vessels a path even though they have NO `tracks` hypertable rows.

The **ingestor** consumes the watchlist without a per-message Redis call: in `_tick_loop` (already runs every 2s), one `SMEMBERS`, diff against a cached `self._watchlist` set; for newly-added MMSIs, **flush the in-process ring buffer** (`state.get_track(mmsi)`, ≤256 fixes) into the track queue via a new `writer.enqueue_fix_raw(ts,mmsi,lat,lon)` (NULLs for sog/cog/heading/nav_status/zone — `_INSERT_TRACK` accepts them). Then widen the persistence gate at `main.py`:
```python
persist = in_zone or (vstate.mmsi in self._watchlist)
if not upd.is_static and upd.lat is not None and persist: writer.enqueue_fix(vstate)
```
Result: a freshly-flagged global vessel gets an **instant retroactive trail** (ring flush) and **live accrual** thereafter — no schema change, no world-scale writes. The `if not in_zone: continue` for detectors stays (zone forensics unchanged); only persistence widens.

---

## 3. API ADDITIONS (`services/api/api/`)

**WS:** add a third shared tailer `_tail_alerts()` in `ws.py`'s `StreamFanout` (clone `_tail_signals` pattern: `_ensure_group(STREAM_FLEET_ALERTS)` under `CONSUMER_GROUP_API_FLEET`, `XREADGROUP`, `_broadcast`). New WS frame `FleetAlertMsg{kind:"fleet_alert", alert: FleetAlert}` in `ws.py` contract + `WSMessage` union (Python + `web/lib/contracts.ts`).

**REST (`routes.py`):**
- `POST /vessels/{mmsi}/flag` `{category?, reason?}` → `SADD watchlist:priority` + `HSET watchlist:meta`; `DELETE` → `SREM`.
- `GET /watchlist` → `list[FleetAlertLite]`-ish flagged vessels (drives a "tracked ships" view).
- `GET /vessels/{mmsi}/track?max=` → full path; **merges** `tracks` (PG) with `fleet:track:{mmsi}` (Redis breadcrumbs) so global flagged vessels return a path. Keeps the dossier JSON bounded (dossier embeds last 500; this serves the full polyline on demand).
- **Enrich `get_vessel`** (additive, back-compat) — `VesselDossier` gains: `lat/lon/sog/cog/heading`, `course_compass` (new `trident_geo/compass.py: cog_to_compass`), `origin`+`origin_ts` (=`track[0]`), `track_distance_nm`+`avg_sog` (great-circle sum), `flag_of_convenience` (`is_flag_of_convenience`), `on_watchlist`+`watch_category`+`flagged_ts` (one `HGET watchlist:meta`). All computed from data already fetched + one O(1) Redis read.

---

## 4. WEB CHANGES (`web/`)

**Contracts/colors/store:** add `ThreatCategory`, `FleetAlert`, `THREAT_CATEGORY` label/color map, `fleet_alert` to `WSMessage` (`contracts.ts`); `CATEGORY_HEX`/band colors + `compass8(cog)` (`colors.ts`); to `store.ts` add `alerts`, `alertCounts`, `mutedCategories`, `notifyEnabled`, `selectedVesselMmsi`, `investigateNonce`, `selectedTrack` + actions `pushAlert` (ring-buffer like `pushIncident`, drop muted, bump count), `toggleCategory`, `investigate(mmsi)` (sets `selectedVesselMmsi`+`dossierMmsi`, bumps nonce), `setSelectedTrack`, `clearSelectedVessel`.

**WS (`ws.ts`):** add `onAlert` to `FeedCallbacks`; `case "fleet_alert": this.cbs.onAlert?.(msg.alert); if(sev≥.8) spawnPingForMmsi(...)`. Wire `onAlert:(a)=>useStore.getState().pushAlert(a)` + `maybeNotify(a)` in `Map.tsx` feed construction.

**Notification feed — `AlertFeed.tsx` (new):** color-coded plain-language cards (category chip + severity dot + relative time + agent headline + name/flag/zone), filter strip of 8 category chips with live counts (toggle `mutedCategories`), newest-first with a "By severity" sort toggle. Subscribes to `s.alerts` only (event-driven, like `IncidentFeed`). Each card `onClick={()=>investigate(a.mmsi)}` + explicit "Track ship ›" button. Stacks **above** `IncidentFeed` in the right rail.

**Live ticker — `AlertTicker.tsx` (new or extend `SignalTicker`):** second primary marquee of plain-language headlines, category-colored, reusing the CSS `.ticker-track` animation (no JS per frame); rows clickable → `investigate`. Demote raw detector ticker to a dim secondary lane. Plus `web/lib/notify.ts`: Browser Notifications, opt-in bell, gated (`severity≥.7`, tab hidden, `≥8s` throttle, dedupe by id, `tag:trident-{mmsi}`); click → `window.focus()` + `investigate(mmsi)`. One investigate code path for card, ticker, and OS notification.

**Click-to-investigate map layers (`layers.ts` + `Map.tsx`):**
- `Map.tsx:240` change: `selectedMmsi: selVessel ?? (selInc ? selInc.mmsi : null)` — alert-click and incident-click light up the same vessel via the **existing** highlight (navy recolor + enlarge + `updateTriggers`). No new highlight system.
- Add investigate effect watching `selectedVesselMmsi`+`investigateNonce`: fly to live render pos (`feed.getVessel(mmsi)`) else dossier's last fix.
- `VesselDossier` fetch pushes `track` → `setSelectedTrack`; `Map.tsx` reads it via `getState()` in the render tick (no extra subscription) and feeds `buildLayers`. Four **single-feature** overlay layers, guarded `selectedTrack.length>=2` (absent during normal browsing): `PathLayer` (red traveled path, `depthTest:false`), `ScatterplotLayer` origin ring ("DEPARTED HERE"), `IconLayer` heading chevron (reuses arrow atlas, rotated to live cog), optional `TripsLayer` comet for direction-of-travel. For global vessels with no PG track, fall back to `feed.getTrails()` for the selected mmsi. O(1) vessels — cannot regress the thousands-of-dots layer.

**Layperson UI (de-jargon, single source of truth `web/lib/labels.ts`):** `SIGNAL_LABEL`/`SIGNAL_HELP`/`TYPOLOGY_LABEL`/`THREAT_WORD`/`SEVERITY_WORD`, imported by all rails. Concrete changes: `LeftRail` sections → "Key Shipping Routes / Ships on screen / Live Alerts" with plain-language signal rows + expert tooltips + an all-zero empty state; `IncidentFeed` cards lead with ship name + plain typology + Low/Med/High/Severe chip + "See this ship ›"; `StatusBar` tagline "Global Ship Watch", "Live" status (msg/s in tooltip), zone chips show route name + Calm/Busy/Crowded/Alert word; `SignalTicker.fmt()` plain text + clickable; `VesselDossier` "Ship Details", friendly field labels (ID/Length/Width/Destination), reassuring no-record copy, Start/Now/heading markers on `TrackSpark`; new `Legend.tsx` (floating bottom-left, collapsible, driven by `colors.ts` constants — the single biggest gap today, nothing explains the dot colors); new `ThreatSummary.tsx` ("All clear" / "3 ships flagged" headline, clickable to worst); new `FirstRun.tsx` (3 coach-marks, `localStorage`-gated, `?` re-open button). Kill `animate-flicker` empty states (reads as broken to novices). All surfaces reuse existing tokens (`--panel`, `--hairline`, `--amber` navy, `--alert`) — no theme change, map stays the protagonist.

---

## 5. PHASED DEPLOY SEQUENCE (each independently deployable + browser-verifiable)

**Phase 0 — Layperson polish (zero backend, ship today, instant visible value).**
Files: `web/lib/labels.ts` (new), `web/components/map/Legend.tsx` (new), `web/components/rails/{LeftRail,IncidentFeed,VesselDossier}.tsx`, `web/components/statusbar/StatusBar.tsx`, `web/components/ticker/SignalTicker.tsx`, `web/components/rails/ThreatSummary.tsx` (new), `web/components/onboarding/FirstRun.tsx` (new), `web/app/page.tsx`.
Verify: open app → Legend explains dot colors; rails read in plain English; first-run overlay appears once; existing map/incidents still work; 30fps unaffected (chrome-only changes).

**Phase 1 — Fleetscan + alert stream + ticker/feed (the core ask: agents + notifications).**
Files: `packages/contracts/trident_contracts/{enums.py(+ThreatCategory), fleet_alert.py(new), __init__.py}`, `packages/common/trident_common/keys.py`, `services/fleetscan/*` (new), `services/api/api/ws.py` (`_tail_alerts` + `FleetAlertMsg`), `web/lib/{contracts.ts,colors.ts,store.ts,ws.ts}`, `web/components/rails/AlertFeed.tsx` (new), `web/components/ticker/AlertTicker.tsx` (new), `web/app/page.tsx`. Deploy fleetscan as a new HF process.
Verify: with live AIS, alerts appear in the ticker + AlertFeed naming ships and categories; category filter works; Redis `XLEN trident:fleet_alerts` grows; ingest msg/s and render fps unchanged.

**Phase 2 — Click-to-investigate: fly + path + heading + dossier.**
Files: `web/components/map/{Map.tsx,layers.ts}`, `web/components/rails/VesselDossier.tsx`, `services/api/api/routes.py` (enrich `get_vessel`, `GET /vessels/{mmsi}/track`), `packages/contracts/.../vessel.py` + `web/lib/contracts.ts` (dossier fields), `packages/geo/trident_geo/compass.py` (new).
Verify: click an alert → map flies to the ship, red path + origin ring + heading arrow render, dossier shows position/origin/direction/speed; closing clears overlays; thousands-of-dots layer unaffected (overlay is single-feature, guarded).

**Phase 3 — Watchlist-driven persistence (paths for global flagged ships).**
Files: `services/ingestor/ingestor/{main.py,writer.py}`, `services/cognition/cognition/persistence.py` (auto-flag SADD), `services/api/api/routes.py` (`POST/DELETE /vessels/{mmsi}/flag`, `/watchlist`, merge `fleet:track` into `/track`), `services/fleetscan/fleetscan/breadcrumbs.py` (write `fleet:track:{mmsi}`).
Verify: flag a global vessel → within ~2s its dossier `/track` returns a retroactive trail (ring flush) that keeps growing; PG `tracks` rows appear for that MMSI; ingest hot path unaffected (watchlist read is 1 `SMEMBERS`/2s, cached).

**Phase 4 — Selective LLM narration.**
Files: `services/fleetscan/fleetscan/narrator.py` (uses `cognition.llm`), `web/components/rails/AlertFeed.tsx` (render `narrative` when present), `web/lib/notify.ts` (new).
Verify: top-3 severe alripts/30s gain a prose `narrative`; force a 429 (or unset `GROQ_API_KEY`) → alerts still flow with `evidence` strings, no errors; OS notifications fire only when tab hidden + `severity≥.7`, throttled.

---

## 6. RISKS + MITIGATIONS

- **Groq 429 (known issue):** NarratorAgent is the only new Groq caller, hard-capped ≤3 calls/30s, circuit-broken with exponential backoff; on 429 ships `narrative=None` and UI uses deterministic `evidence`. `has_llm()==False` → no-op. The entire alert plane works with **zero** Groq calls. Shares the one `ChatGroq` client with cognition, so no new key contention.
- **Performance / smoothness:** all new data is event-driven WS frames (like `signal_tick`), never per-frame React state; ticker is CSS-animated; the selected-vessel path is 4 single-feature layers mounted only on click (guarded), fed from a stable ref, separate from the memoized GPU dot/arrow layers; render loop stays 33ms reading via `getState()`. Highlight reuses the existing `selectedMmsi` machinery (one-line change at `Map.tsx:240`).
- **Global scale:** fleetscan sweep is ~2 Redis ops/500 vessels every 10s + pure-Python rules; in-process memory bounded and pruned on MMSI dropout; breadcrumb rings written only for the few hundred flagged vessels — no world-scale writes. No new DB load except watchlist-scoped `tracks` inserts.
- **Missing ship_type (AIS type 0):** `bucket_for_ship_type(None)→OTHER` already handled; type-dependent agents (DARK_FLEET, STS, GREY_ZONE) skip or down-weight confidence on `OTHER` rather than mislabel; DARK_FLEET has a `draught≥8 + imo` fallback for untyped likely-tankers; UI labels OTHER "Type not broadcast yet".
- **No historical track for newly-flagged global vessels:** solved by the ring-buffer **retroactive flush** at flag-time (instant ≤256-fix trail) + `fleet:track:{mmsi}` Redis breadcrumbs, merged with PG `tracks` in `/track`. If a vessel aged out (>30min) before flagging, ring is empty → dossier shows "Tracking from now" gracefully. True flag-hopping remains undetectable and is **never claimed** — only FOC fact is surfaced as a badge/multiplier.

**Cross-area dependency (single seam):** the `FleetAlert` shape (§2b) is the contract between fleetscan (producer) and API/web (consumers). Lock it in Phase 1; everything else is self-contained and reuses existing camera, highlight, dossier, ticker-CSS, and stream-fanout machinery.