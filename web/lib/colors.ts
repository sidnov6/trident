import { SHIP_BUCKET, type ThreatLevel } from "./contracts";

export type RGBA = [number, number, number, number];

// Ship-type bucket → glow colour. Tanker amber, cargo cyan,
// passenger green, everything else cool grey.
export function shipColor(bucket: number): RGBA {
  switch (bucket) {
    case SHIP_BUCKET.TANKER:
      return [255, 176, 0, 255]; // amber
    case SHIP_BUCKET.CARGO:
      return [46, 230, 255, 255]; // cyan
    case SHIP_BUCKET.PASSENGER:
      return [31, 214, 95, 255]; // green
    case SHIP_BUCKET.HIGH_SPEED:
      return [120, 230, 255, 255];
    case SHIP_BUCKET.FISHING:
      return [150, 170, 190, 255];
    case SHIP_BUCKET.TUG_SPECIAL:
      return [200, 160, 120, 255];
    default:
      return [120, 140, 160, 255]; // other grey
  }
}

export const SHIP_LABEL: Record<number, string> = {
  [SHIP_BUCKET.OTHER]: "OTHER",
  [SHIP_BUCKET.TANKER]: "TANKER",
  [SHIP_BUCKET.CARGO]: "CARGO",
  [SHIP_BUCKET.PASSENGER]: "PASSENGER",
  [SHIP_BUCKET.FISHING]: "FISHING",
  [SHIP_BUCKET.HIGH_SPEED]: "HIGH-SPEED",
  [SHIP_BUCKET.TUG_SPECIAL]: "TUG/SPECIAL",
};

// Threat ladder (NORAD-style)
export const THREAT_HEX: Record<ThreatLevel, string> = {
  GREEN: "#1fd65f",
  ELEVATED: "#ffd400",
  HIGH: "#ff8a00",
  CRITICAL: "#ff2e3e",
};

export const THREAT_ORDER: ThreatLevel[] = [
  "GREEN",
  "ELEVATED",
  "HIGH",
  "CRITICAL",
];

// Severity 0..1 → hex on the cool→hot ramp
export function severityHex(sev: number): string {
  if (sev >= 0.8) return "#ff2e3e";
  if (sev >= 0.6) return "#ff8a00";
  if (sev >= 0.4) return "#ffd400";
  if (sev >= 0.2) return "#2ee6ff";
  return "#6b7c8c";
}

export const ALERT_RED: RGBA = [255, 46, 62, 255];
export const INFO_CYAN: RGBA = [46, 230, 255, 255];
export const AMBER: RGBA = [255, 176, 0, 255];
