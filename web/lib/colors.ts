import { SHIP_BUCKET, type ThreatLevel } from "./contracts";

export type RGBA = [number, number, number, number];

// Ship-type bucket → marker colour, saturated/dark enough to read on a LIGHT map.
export function shipColor(bucket: number): RGBA {
  switch (bucket) {
    case SHIP_BUCKET.TANKER:
      return [212, 56, 13, 255]; // strong orange-red
    case SHIP_BUCKET.CARGO:
      return [29, 78, 216, 255]; // blue
    case SHIP_BUCKET.PASSENGER:
      return [22, 163, 74, 255]; // green
    case SHIP_BUCKET.HIGH_SPEED:
      return [124, 58, 237, 255]; // purple
    case SHIP_BUCKET.FISHING:
      return [8, 145, 178, 255]; // teal
    case SHIP_BUCKET.TUG_SPECIAL:
      return [180, 83, 9, 255]; // amber-brown
    default:
      return [71, 85, 105, 255]; // slate
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
  GREEN: "#16a34a",
  ELEVATED: "#d97706",
  HIGH: "#ea580c",
  CRITICAL: "#dc2626",
};

export const THREAT_ORDER: ThreatLevel[] = [
  "GREEN",
  "ELEVATED",
  "HIGH",
  "CRITICAL",
];

// Severity 0..1 → hex on the cool→hot ramp (light-palette)
export function severityHex(sev: number): string {
  if (sev >= 0.8) return "#dc2626";
  if (sev >= 0.6) return "#ea580c";
  if (sev >= 0.4) return "#d97706";
  if (sev >= 0.2) return "#0e9aa7";
  return "#5a6b80";
}

export const ALERT_RED: RGBA = [220, 38, 38, 255];
export const INFO_CYAN: RGBA = [14, 154, 167, 255];
export const AMBER: RGBA = [31, 95, 191, 255];
