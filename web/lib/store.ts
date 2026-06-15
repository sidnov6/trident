"use client";

import { create } from "zustand";
import type { Incident, SignalLite } from "./contracts";
import type { ZoneStat, HealthState } from "./types";

const MAX_SIGNALS = 200;
const MAX_INCIDENTS = 100;

interface UIState {
  // selection / camera intent
  selectedZone: string;
  selectedIncidentId: string | null;
  dossierMmsi: number | null;
  flyTo: { lon: number; lat: number; zoom?: number; ts: number } | null;
  replayMode: boolean;

  // live data
  incidents: Incident[];
  signals: SignalLite[];
  zones: Record<string, ZoneStat>;
  health: HealthState;
  vesselCount: number;
  signalCounts: Record<string, number>;
  // live ship-type histogram for the current viewport (index = ShipTypeBucket)
  viewportBuckets: number[];
  viewportBbox: [number, number, number, number] | null; // minLat,minLon,maxLat,maxLon

  // actions
  setSelectedZone: (z: string) => void;
  selectIncident: (id: string | null) => void;
  openDossier: (mmsi: number | null) => void;
  requestFlyTo: (lon: number, lat: number, zoom?: number) => void;
  setReplayMode: (on: boolean) => void;

  pushIncident: (i: Incident) => void;
  pushSignal: (s: SignalLite) => void;
  setZone: (z: ZoneStat) => void;
  setZones: (zs: ZoneStat[]) => void;
  setHealth: (h: HealthState) => void;
  setVesselCount: (n: number) => void;
  setViewportBuckets: (b: number[]) => void;
  setViewportBbox: (b: [number, number, number, number]) => void;
}

export const useStore = create<UIState>((set) => ({
  selectedZone: "suez",
  selectedIncidentId: null,
  dossierMmsi: null,
  flyTo: null,
  replayMode: false,

  incidents: [],
  signals: [],
  zones: {},
  health: { online: false, msgPerSec: 0, lastMsgMs: 0 },
  vesselCount: 0,
  signalCounts: {},
  viewportBuckets: [0, 0, 0, 0, 0, 0, 0],
  viewportBbox: null,

  setSelectedZone: (z) => set({ selectedZone: z }),
  selectIncident: (id) => set({ selectedIncidentId: id }),
  openDossier: (mmsi) => set({ dossierMmsi: mmsi }),
  requestFlyTo: (lon, lat, zoom) =>
    set({ flyTo: { lon, lat, zoom, ts: Date.now() } }),
  setReplayMode: (on) => set({ replayMode: on }),

  pushIncident: (i) =>
    set((s) => {
      const without = s.incidents.filter((x) => x.id !== i.id);
      return { incidents: [i, ...without].slice(0, MAX_INCIDENTS) };
    }),

  pushSignal: (sig) =>
    set((s) => {
      const counts = { ...s.signalCounts };
      counts[sig.type] = (counts[sig.type] ?? 0) + 1;
      return {
        signals: [sig, ...s.signals].slice(0, MAX_SIGNALS),
        signalCounts: counts,
      };
    }),

  setZone: (z) => set((s) => ({ zones: { ...s.zones, [z.zone]: z } })),
  setZones: (zs) =>
    set(() => {
      const map: Record<string, ZoneStat> = {};
      for (const z of zs) map[z.zone] = z;
      return { zones: map };
    }),
  setHealth: (h) => set({ health: h }),
  setVesselCount: (n) => set({ vesselCount: n }),
  setViewportBuckets: (b) => set({ viewportBuckets: b }),
  setViewportBbox: (b) => set({ viewportBbox: b }),
}));
