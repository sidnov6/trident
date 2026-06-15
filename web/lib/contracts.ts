// TRIDENT shared contracts — TypeScript mirror of trident_contracts (Python).
// Keep in sync with packages/contracts/trident_contracts/*.py

export type SignalType =
  | "DARK_VESSEL"
  | "LOITERING"
  | "POSITION_JUMP"
  | "IDENTITY_CONFLICT"
  | "CONGESTION"
  | "GEOFENCE_BREACH"
  | "REROUTE"
  | "UTURN";

export type Typology =
  | "SANCTIONS_EVASION"
  | "STS_TRANSFER"
  | "SMUGGLING_COVER"
  | "NAV_HAZARD"
  | "MILITARY_ACTIVITY"
  | "BENIGN";

export type IncidentStatus = "open" | "confirmed" | "dismissed" | "actioned";
export type ThreatLevel = "GREEN" | "ELEVATED" | "HIGH" | "CRITICAL";

// Ship-type colour buckets (ShipTypeBucket)
export const SHIP_BUCKET = {
  OTHER: 0,
  TANKER: 1,
  CARGO: 2,
  PASSENGER: 3,
  FISHING: 4,
  HIGH_SPEED: 5,
  TUG_SPECIAL: 6,
} as const;

// Status bitfield in VesselLite.st
export const STATUS_BIT = {
  DARK: 1 << 0,
  LOITERING: 1 << 1,
  WATCHLIST: 1 << 2,
  GEOFENCE: 1 << 3,
  SPOOF: 1 << 4,
} as const;

export interface VesselLite {
  m: number; // mmsi
  la: number; // latitude
  lo: number; // longitude
  s: number; // sog (knots)
  c: number; // cog (degrees)
  t: number; // ship_type bucket
  f: number; // last_fix_ts (epoch s)
  st: number; // status bitfield
}

export interface SignalLite {
  id: string;
  ts: number;
  type: SignalType;
  mmsi: number;
  zone: string;
  severity: number;
}

export interface Incident {
  id: string;
  mmsi: number;
  zone: string;
  typology: Typology;
  severity: number;
  confidence: number;
  status: IncidentStatus;
  opened_at: number;
  position?: [number, number] | null;
  summary: string;
  market_note: string;
  signals?: unknown[];
  sentinel?: unknown;
  analyst?: AnalystOutput | null;
  desk?: DeskOutput | null;
}

export interface AnalystOutput {
  typology: Typology;
  severity: number;
  confidence: number;
  summary: string;
  reasoning_trace: string[];
  sts_partner_mmsi?: number | null;
  sanctions_match?: Record<string, unknown> | null;
  sar_confirmation?: Record<string, unknown> | null;
  weather_context?: Record<string, unknown> | null;
  osint_context?: Record<string, unknown> | null;
}

export interface DeskOutput {
  market_note: string;
  commodities: string[];
  reroute_days?: number | null;
  rate_direction?: string | null;
  brent_sensitivity?: string | null;
  retrieved_facts: string[];
  inferences: string[];
}

export interface VesselDossier {
  mmsi: number;
  imo?: number | null;
  name?: string | null;
  flag?: string | null;
  ship_type?: number | null;
  destination?: string | null;
  draught?: number | null;
  length?: number | null;
  beam?: number | null;
  first_seen_ts?: number | null;
  last_fix_ts?: number | null;
  track: [number, number, number][]; // (ts, lat, lon)
  incident_ids: string[];
}

export type WSMessage =
  | { kind: "vessel_delta"; vessels: VesselLite[]; ts: number }
  | { kind: "signal_tick"; signal: SignalLite }
  | { kind: "incident"; incident: Incident }
  | {
      kind: "zone_stats";
      zone: string;
      count: number;
      z: number;
      transit_min?: number | null;
      threat_level: ThreatLevel;
    };
