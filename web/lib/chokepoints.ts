import type { Chokepoint } from "./types";

// Mirror of packages/geo/trident_geo/chokepoints.py — kept here so the
// left rail / fly-to works with no backend.
export const CHOKEPOINTS: Chokepoint[] = [
  {
    id: "suez",
    name: "Suez Canal",
    center: [30.55, 32.35],
    bbox: [
      [29.85, 32.25],
      [31.35, 32.65],
    ],
  },
  {
    id: "hormuz",
    name: "Strait of Hormuz",
    center: [26.5, 56.2],
    bbox: [
      [25.9, 55.3],
      [27.1, 57.1],
    ],
  },
  {
    id: "bab_el_mandeb",
    name: "Bab-el-Mandeb",
    center: [12.7, 43.4],
    bbox: [
      [12.3, 43.1],
      [13.1, 43.7],
    ],
  },
  {
    id: "malacca",
    name: "Strait of Malacca",
    center: [3.5, 101.0],
    bbox: [
      [1.0, 98.0],
      [6.0, 104.0],
    ],
  },
  {
    id: "panama",
    name: "Panama Canal",
    center: [9.15, -79.75],
    bbox: [
      [8.85, -80.05],
      [9.45, -79.45],
    ],
  },
  {
    id: "bosphorus",
    name: "Bosphorus",
    center: [41.1, 29.05],
    bbox: [
      [40.9, 28.9],
      [41.3, 29.2],
    ],
  },
];

export const CHOKEPOINTS_BY_ID: Record<string, Chokepoint> = Object.fromEntries(
  CHOKEPOINTS.map((c) => [c.id, c])
);

// Suez is the protagonist — default camera.
export const DEFAULT_CENTER: [number, number] = [32.35, 30.55]; // [lon, lat]
export const DEFAULT_ZOOM = 8.2;
