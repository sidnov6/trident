// Minimal GeoJSON typing + loader for the Suez zone outlines.
export type { Incident } from "@/lib/contracts";

export interface GeometryLike {
  type: "Polygon" | "MultiPolygon" | string;
  coordinates: unknown;
}

export interface FeatureLike {
  type: string;
  features?: {
    type: string;
    properties?: Record<string, unknown> | null;
    geometry?: GeometryLike | null;
  }[];
}

export async function loadGeo(
  name: string
): Promise<FeatureLike | undefined> {
  try {
    const res = await fetch(`/geo/${name}.geojson`, { cache: "force-cache" });
    if (!res.ok) return undefined;
    return (await res.json()) as FeatureLike;
  } catch {
    return undefined;
  }
}
