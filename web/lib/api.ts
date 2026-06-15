// REST helpers for the TRIDENT api service. Everything is defensive:
// a failed fetch resolves to a sensible empty value, never throws to the UI.
import type {
  Incident,
  VesselLite,
  VesselDossier,
  SignalLite,
} from "./contracts";
import type { ZoneStat, HealthState } from "./types";

// Same-origin mode: when NEXT_PUBLIC_API_BASE is "" (set explicitly at build,
// e.g. the single-container Hugging Face Space where one server serves the UI,
// the REST API and the WS on one port), use relative URLs so the browser talks
// back to whatever origin served the page. Otherwise fall back to localhost dev.
const _envBase = process.env.NEXT_PUBLIC_API_BASE;
export const API_BASE = _envBase !== undefined ? _envBase : "http://localhost:8000";
export const REPLAY_BASE =
  process.env.NEXT_PUBLIC_REPLAY_BASE ?? "http://localhost:8100";

async function getJSON<T>(url: string, fallback: T, timeoutMs = 4000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: ctrl.signal, cache: "no-store" });
    if (!res.ok) return fallback;
    return (await res.json()) as T;
  } catch {
    return fallback;
  } finally {
    clearTimeout(timer);
  }
}

export function getVessels(zone?: string): Promise<VesselLite[]> {
  const q = zone ? `?zone=${encodeURIComponent(zone)}` : "";
  return getJSON<VesselLite[]>(`${API_BASE}/vessels${q}`, []);
}

export function getDossier(mmsi: number): Promise<VesselDossier | null> {
  return getJSON<VesselDossier | null>(`${API_BASE}/vessels/${mmsi}`, null);
}

export function getIncidents(): Promise<Incident[]> {
  return getJSON<Incident[]>(`${API_BASE}/incidents`, []);
}

export function getIncident(id: string): Promise<Incident | null> {
  return getJSON<Incident | null>(`${API_BASE}/incidents/${id}`, null);
}

export function getSignals(): Promise<SignalLite[]> {
  return getJSON<SignalLite[]>(`${API_BASE}/signals`, []);
}

// /zones shape isn't pinned in the contract; accept either ZoneStat[] or a map.
export async function getZones(): Promise<ZoneStat[]> {
  const raw = await getJSON<unknown>(`${API_BASE}/zones`, []);
  return normalizeZones(raw);
}

export function normalizeZones(raw: unknown): ZoneStat[] {
  const now = Date.now();
  const coerce = (z: Record<string, unknown>): ZoneStat => ({
    zone: String(z.zone ?? z.id ?? ""),
    count: Number(z.count ?? 0),
    z: Number(z.z ?? 0),
    transit_min: (z.transit_min as number | null | undefined) ?? null,
    threat_level: (z.threat_level as ZoneStat["threat_level"]) ?? "GREEN",
    ts: now,
  });
  if (Array.isArray(raw)) {
    return raw.map((z) => coerce(z as Record<string, unknown>));
  }
  if (raw && typeof raw === "object") {
    return Object.values(raw as Record<string, unknown>).map((z) =>
      coerce(z as Record<string, unknown>)
    );
  }
  return [];
}

// /health: tolerate several field names for messages/sec.
export async function getHealth(): Promise<HealthState> {
  const raw = await getJSON<Record<string, unknown> | null>(
    `${API_BASE}/health`,
    null,
    2500
  );
  if (!raw) return { online: false, msgPerSec: 0, lastMsgMs: 0 };
  const mps = Number(
    raw.messages_per_sec ??
      raw.msg_per_sec ??
      raw.mps ??
      raw.rate ??
      0
  );
  const ok =
    raw.status === "ok" ||
    raw.ok === true ||
    raw.healthy === true ||
    raw.status === "healthy" ||
    raw.status === undefined; // a 200 with unknown body still counts as online
  return { online: !!ok, msgPerSec: isFinite(mps) ? mps : 0, lastMsgMs: Date.now() };
}

// ── Replay (forensic mode) ────────────────────────────────────────────────
export function replayStreamURL(opts: {
  mmsi?: number;
  from?: number;
  to?: number;
  speed?: number;
}): string {
  const base = REPLAY_BASE.replace(/^http/, "ws");
  const p = new URLSearchParams();
  if (opts.mmsi != null) p.set("mmsi", String(opts.mmsi));
  if (opts.from != null) p.set("from", String(opts.from));
  if (opts.to != null) p.set("to", String(opts.to));
  if (opts.speed != null) p.set("speed", String(opts.speed));
  return `${base}/replay/stream?${p.toString()}`;
}
