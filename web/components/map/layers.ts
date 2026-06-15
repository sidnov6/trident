// deck.gl layer factory. Pure functions of (render state) → layer array so the
// Map component can rebuild cheaply each frame. Carried by a single interleaved
// MapboxOverlay.

import {
  IconLayer,
  ScatterplotLayer,
  PolygonLayer,
  ArcLayer,
} from "@deck.gl/layers";
import { TripsLayer } from "@deck.gl/geo-layers";
import { HeatmapLayer } from "@deck.gl/aggregation-layers";
import type { Layer } from "@deck.gl/core";

import type { RenderVessel, ZoneStat, DarkPing } from "@/lib/types";
import type { Incident, FeatureLike } from "./geo";
import { shipColor, ALERT_RED } from "@/lib/colors";
import { STATUS_BIT } from "@/lib/contracts";
import { vesselArrowDataURI, vesselIconMapping } from "./icons";

export interface LayerInput {
  vessels: RenderVessel[];
  trails: { mmsi: number; path: [number, number][]; timestamps: number[] }[];
  pings: DarkPing[];
  zones: Record<string, ZoneStat>;
  incidents: Incident[];
  geo: { fairway?: FeatureLike; exclusion?: FeatureLike; anchorage?: FeatureLike };
  nowMs: number;
  zoom: number;
  selectedMmsi: number | null;
  onVesselClick: (mmsi: number) => void;
}

const GEO_OUTLINE: Record<string, [number, number, number]> = {
  fairway: [46, 230, 255],
  exclusion: [255, 46, 62],
  anchorage: [255, 176, 0],
};

function polygonsFromGeo(
  f: FeatureLike | undefined
): { polygon: number[][]; kind: string }[] {
  if (!f || !f.features) return [];
  const out: { polygon: number[][]; kind: string }[] = [];
  for (const feat of f.features) {
    const kind = String(feat.properties?.kind ?? "fairway");
    const g = feat.geometry;
    if (!g) continue;
    if (g.type === "Polygon") {
      const coords = g.coordinates as number[][][];
      out.push({ polygon: coords[0], kind });
    } else if (g.type === "MultiPolygon") {
      for (const poly of g.coordinates as number[][][][]) {
        out.push({ polygon: poly[0], kind });
      }
    }
  }
  return out;
}

export function buildLayers(input: LayerInput): Layer[] {
  const {
    vessels,
    trails,
    pings,
    zones,
    incidents,
    geo,
    nowMs,
    zoom,
    selectedMmsi,
    onVesselClick,
  } = input;

  const layers: Layer[] = [];

  // 1. Geofences — thin glowing outlines.
  const geoPolys = [
    ...polygonsFromGeo(geo.fairway),
    ...polygonsFromGeo(geo.exclusion),
    ...polygonsFromGeo(geo.anchorage),
  ];
  if (geoPolys.length) {
    layers.push(
      new PolygonLayer<{ polygon: number[][]; kind: string }>({
        id: "geofences",
        data: geoPolys,
        getPolygon: (d) => d.polygon,
        stroked: true,
        filled: true,
        getLineColor: (d) => {
          const c = GEO_OUTLINE[d.kind] ?? [120, 140, 160];
          return [c[0], c[1], c[2], 200];
        },
        getFillColor: (d) => {
          const c = GEO_OUTLINE[d.kind] ?? [120, 140, 160];
          return [c[0], c[1], c[2], 18];
        },
        getLineWidth: 2,
        lineWidthUnits: "pixels",
      })
    );
  }

  // 2. Congestion heatmap from zone_stats — bloom at each chokepoint center.
  const heatPts = Object.values(zones)
    .map((z) => {
      const center = ZONE_CENTERS[z.zone];
      if (!center) return null;
      return { position: [center[1], center[0]] as [number, number], weight: z.count };
    })
    .filter(Boolean) as { position: [number, number]; weight: number }[];
  if (heatPts.length) {
    layers.push(
      new HeatmapLayer<{ position: [number, number]; weight: number }>({
        id: "congestion",
        data: heatPts,
        getPosition: (d) => d.position,
        getWeight: (d) => d.weight,
        radiusPixels: 120,
        intensity: 1,
        threshold: 0.05,
        colorRange: [
          [0, 40, 60, 0],
          [0, 80, 110, 80],
          [46, 160, 200, 140],
          [255, 176, 0, 180],
          [255, 138, 0, 210],
          [255, 46, 62, 240],
        ],
      })
    );
  }

  // 3. Trails — animated fading wake.
  if (trails.length) {
    layers.push(
      new TripsLayer<(typeof trails)[number]>({
        id: "trails",
        data: trails,
        getPath: (d) => d.path,
        getTimestamps: (d) => d.timestamps,
        getColor: [46, 230, 255],
        opacity: 0.55,
        widthMinPixels: 1.5,
        rounded: true,
        fadeTrail: true,
        trailLength: 120_000,
        currentTime: nowMs,
      })
    );
  }

  // 4. Dark pings — pulsing expanding red rings.
  if (pings.length) {
    const PING_TTL = 9000;
    layers.push(
      new ScatterplotLayer<DarkPing>({
        id: "dark-pings",
        data: pings,
        getPosition: (d) => [d.lon, d.lat],
        getRadius: (d) => {
          const age = (nowMs - d.born) / PING_TTL; // 0..1
          return 200 + age * 4200;
        },
        radiusUnits: "meters",
        stroked: true,
        filled: false,
        getLineColor: (d) => {
          const age = (nowMs - d.born) / PING_TTL;
          const a = Math.max(0, 1 - age) * 230;
          return [255, 46, 62, a];
        },
        lineWidthMinPixels: 2,
        updateTriggers: { getRadius: nowMs, getLineColor: nowMs },
      })
    );
  }

  // 5. STS / reroute arcs from confirmed incidents.
  const arcs: { from: [number, number]; to: [number, number]; sev: number }[] = [];
  for (const inc of incidents) {
    const partner = inc.analyst?.sts_partner_mmsi;
    const a = inc.position
      ? ([inc.position[1], inc.position[0]] as [number, number])
      : vesselPos(vessels, inc.mmsi);
    if (!a) continue;
    if (partner != null) {
      const b = vesselPos(vessels, partner);
      if (b) arcs.push({ from: a, to: b, sev: inc.severity });
    }
  }
  if (arcs.length) {
    layers.push(
      new ArcLayer<(typeof arcs)[number]>({
        id: "sts-arcs",
        data: arcs,
        getSourcePosition: (d) => d.from,
        getTargetPosition: (d) => d.to,
        getSourceColor: [255, 46, 62, 200],
        getTargetColor: [255, 176, 0, 200],
        getWidth: (d) => 1.5 + d.sev * 3,
        getHeight: 0.4,
      })
    );
  }

  // 6. Vessels — arrow icons rotated to cog, tinted by ship type.
  const iconSize = Math.max(14, Math.min(30, 8 + zoom * 1.8));
  layers.push(
    new IconLayer<RenderVessel>({
      id: "vessels",
      data: vessels,
      pickable: true,
      iconAtlas: vesselArrowDataURI(),
      iconMapping: vesselIconMapping(),
      getIcon: () => "arrow",
      getPosition: (d) => [d.rLon, d.rLat],
      getAngle: (d) => -d.c, // deck rotates CCW; cog is CW from north
      getSize: (d) =>
        d.m === selectedMmsi ? iconSize * 1.6 : iconSize,
      getColor: (d) => {
        if (d.st & STATUS_BIT.DARK) return ALERT_RED;
        const c = shipColor(d.t);
        return d.m === selectedMmsi ? [255, 255, 255, 255] : c;
      },
      sizeUnits: "pixels",
      billboard: true,
      onClick: (info) => {
        const d = info.object as RenderVessel | undefined;
        if (d) onVesselClick(d.m);
      },
      updateTriggers: {
        getColor: selectedMmsi,
        getSize: [selectedMmsi, iconSize],
      },
      })
  );

  return layers;
}

function vesselPos(
  vessels: RenderVessel[],
  mmsi: number
): [number, number] | null {
  const v = vessels.find((x) => x.m === mmsi);
  return v ? [v.rLon, v.rLat] : null;
}

// Centers [lat, lon] for heatmap placement — mirrors chokepoints.
const ZONE_CENTERS: Record<string, [number, number]> = {
  suez: [30.55, 32.35],
  hormuz: [26.5, 56.2],
  bab_el_mandeb: [12.7, 43.4],
  malacca: [3.5, 101.0],
  panama: [9.15, -79.75],
  bosphorus: [41.1, 29.05],
};
