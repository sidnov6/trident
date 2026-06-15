// Re-export the frozen contracts as the app's single source of truth.
export * from "./contracts";

import type { ThreatLevel } from "./contracts";

// ── App-local view models (not on the wire) ───────────────────────────────

// A vessel as rendered by the map: the last truth fix plus the
// dead-reckoned / lerped render position the rAF loop maintains.
export interface RenderVessel {
  m: number; // mmsi
  // last known truth
  la: number;
  lo: number;
  s: number; // sog kn
  c: number; // cog deg
  t: number; // ship_type bucket
  f: number; // last_fix_ts (epoch s)
  st: number; // status bitfield
  // interpolated render position [lon, lat]
  rLon: number;
  rLat: number;
  // lerp anchor: where we were when the last delta arrived (epoch ms) + that pos
  lerpFromLon: number;
  lerpFromLat: number;
  lerpStartMs: number;
}

export interface Chokepoint {
  id: string;
  name: string;
  center: [number, number]; // [lat, lon]
  bbox: [[number, number], [number, number]]; // [[swLat,swLon],[neLat,neLon]]
}

export interface ZoneStat {
  zone: string;
  count: number;
  z: number;
  transit_min?: number | null;
  threat_level: ThreatLevel;
  ts: number;
}

export interface HealthState {
  online: boolean;
  msgPerSec: number;
  lastMsgMs: number;
}

export interface TrailPoint {
  lon: number;
  lat: number;
  t: number; // ms timestamp
}

export interface DarkPing {
  id: string;
  lon: number;
  lat: number;
  born: number; // ms
}
