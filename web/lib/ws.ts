// TRIDENT live feed client.
//
// Responsibilities:
//  1. Connect to ws://.../ws, parse WSMessage, auto-reconnect with backoff.
//  2. Maintain coalesced latest-state-per-MMSI (latest wins, never a backlog).
//  3. Dead-reckon every vessel forward each animation frame from sog+cog, and
//     lerp the rendered position toward a fresh fix over ~300ms (no teleport).
//  4. Maintain a short per-MMSI trail history for the TripsLayer wake.
//  5. Surface signals / incidents / zone_stats / dark-pings via callbacks.
//
// A single requestAnimationFrame loop drives all interpolation; deck.gl layers
// read getRenderVessels() each frame.

import type {
  WSMessage,
  VesselLite,
  SignalLite,
  Incident,
} from "./contracts";
import { STATUS_BIT } from "./contracts";
import type { RenderVessel, ZoneStat, TrailPoint, DarkPing } from "./types";

const LERP_MS = 300; // glide toward truth over this window
const TRAIL_MS = 180_000; // keep ~3 min of wake
const TRAIL_MAX = 60; // hard cap per vessel
const PING_TTL_MS = 9000; // dark-ping ring lifetime
const VESSEL_TTL_MS = 12_000; // drop a vessel not re-pushed within this (left viewport)

// project(v, now) — the spec dead-reckoning math. `now` is epoch ms.
// v carries truth fix (la, lo, s sog kn, c cog deg, f fix epoch s).
export function project(
  v: { la: number; lo: number; s: number; c: number; f: number },
  nowMs: number
): [number, number] {
  const dt = Math.max(0, nowMs / 1000 - v.f); // seconds since fix
  const mps = v.s * 0.514444; // knots → m/s
  const dist = mps * dt;
  const brng = (v.c * Math.PI) / 180;
  const dLat = (dist * Math.cos(brng)) / 111111;
  const cosLa = Math.cos((v.la * Math.PI) / 180) || 1e-6;
  const dLon = (dist * Math.sin(brng)) / (111111 * cosLa);
  return [v.lo + dLon, v.la + dLat]; // [lon, lat]
}

export interface FeedCallbacks {
  onSignal?: (s: SignalLite) => void;
  onIncident?: (i: Incident) => void;
  onZoneStats?: (z: ZoneStat) => void;
  onStatus?: (online: boolean) => void;
  onMsg?: () => void; // any message — used for msgs/sec metering
}

export class TridentFeed {
  private url: string;
  private ws: WebSocket | null = null;
  private cbs: FeedCallbacks;
  private vessels = new Map<number, RenderVessel>();
  private trails = new Map<number, TrailPoint[]>();
  private pings: DarkPing[] = [];
  private rafId = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private backoff = 1000;
  private closed = false;
  private dirty = false;
  private lastViewport: [number, number, number, number] | null = null;

  constructor(url: string, cbs: FeedCallbacks = {}) {
    this.url = url;
    this.cbs = cbs;
  }

  start() {
    this.closed = false;
    this.connect();
    const loop = () => {
      this.tick(Date.now());
      this.rafId = requestAnimationFrame(loop);
    };
    this.rafId = requestAnimationFrame(loop);
  }

  stop() {
    this.closed = true;
    if (this.rafId) cancelAnimationFrame(this.rafId);
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.ws = null;
  }

  /** Tell the server which region the camera is showing so it streams only
   *  the ships in view. bbox = [minLat, minLon, maxLat, maxLon]. */
  sendViewport(bbox: [number, number, number, number]) {
    this.lastViewport = bbox;
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify({ kind: "viewport", bbox }));
      } catch {
        /* will re-send on reconnect */
      }
    }
  }

  private connect() {
    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws.onopen = () => {
      this.backoff = 1000;
      this.cbs.onStatus?.(true);
      // Re-assert the camera so the server resumes streaming the right region.
      if (this.lastViewport) this.sendViewport(this.lastViewport);
    };
    this.ws.onclose = () => {
      this.cbs.onStatus?.(false);
      this.scheduleReconnect();
    };
    this.ws.onerror = () => {
      // onclose will fire; nothing to do.
    };
    this.ws.onmessage = (ev) => {
      this.cbs.onMsg?.();
      let msg: WSMessage;
      try {
        msg = JSON.parse(ev.data as string) as WSMessage;
      } catch {
        return;
      }
      this.handle(msg);
    };
  }

  private scheduleReconnect() {
    if (this.closed) return;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => this.connect(), this.backoff);
    this.backoff = Math.min(this.backoff * 1.7, 15000);
  }

  private handle(msg: WSMessage) {
    switch (msg.kind) {
      case "vessel_delta":
        for (const v of msg.vessels) this.applyDelta(v);
        this.dirty = true;
        break;
      case "signal_tick":
        this.cbs.onSignal?.(msg.signal);
        if (msg.signal.type === "DARK_VESSEL") this.spawnPingForMmsi(msg.signal.mmsi);
        break;
      case "incident":
        this.cbs.onIncident?.(msg.incident);
        this.spawnPingForIncident(msg.incident);
        break;
      case "zone_stats":
        this.cbs.onZoneStats?.({
          zone: msg.zone,
          count: msg.count,
          z: msg.z,
          transit_min: msg.transit_min ?? null,
          threat_level: msg.threat_level,
          ts: Date.now(),
        });
        break;
    }
  }

  // Coalesce: latest fix wins. Set up the lerp anchor from the current render
  // pos so the glyph glides to truth rather than snapping.
  private applyDelta(v: VesselLite) {
    const now = Date.now();
    const prev = this.vessels.get(v.m);
    const fromLon = prev ? prev.rLon : v.lo;
    const fromLat = prev ? prev.rLat : v.la;
    const rv: RenderVessel = {
      m: v.m,
      la: v.la,
      lo: v.lo,
      s: v.s,
      c: v.c,
      t: v.t,
      f: v.f,
      st: v.st,
      rLon: fromLon,
      rLat: fromLat,
      lerpFromLon: fromLon,
      lerpFromLat: fromLat,
      lerpStartMs: now,
      rx: now,
    };
    this.vessels.set(v.m, rv);

    // Append to trail history (truth position).
    let tr = this.trails.get(v.m);
    if (!tr) {
      tr = [];
      this.trails.set(v.m, tr);
    }
    tr.push({ lon: v.lo, lat: v.la, t: now });
    while (tr.length > TRAIL_MAX || (tr.length > 1 && now - tr[0].t > TRAIL_MS)) {
      tr.shift();
    }

    // Dark bit flipping on → ping.
    if (prev && !(prev.st & STATUS_BIT.DARK) && v.st & STATUS_BIT.DARK) {
      this.pings.push({ id: `d${v.m}-${now}`, lon: v.lo, lat: v.la, born: now });
    }
  }

  private spawnPingForMmsi(mmsi: number) {
    const v = this.vessels.get(mmsi);
    const now = Date.now();
    if (v) this.pings.push({ id: `s${mmsi}-${now}`, lon: v.rLon, lat: v.rLat, born: now });
  }

  private spawnPingForIncident(i: Incident) {
    const now = Date.now();
    let lon: number | undefined;
    let lat: number | undefined;
    if (i.position && i.position.length === 2) {
      lat = i.position[0];
      lon = i.position[1];
    } else {
      const v = this.vessels.get(i.mmsi);
      if (v) {
        lon = v.rLon;
        lat = v.rLat;
      }
    }
    if (lon != null && lat != null) {
      this.pings.push({ id: `i${i.id}-${now}`, lon, lat, born: now });
    }
  }

  // Per-frame: dead-reckon + lerp every vessel; expire pings.
  private tick(now: number) {
    for (const v of this.vessels.values()) {
      const [projLon, projLat] = project(v, now);
      const since = now - v.lerpStartMs;
      if (since < LERP_MS) {
        // Glide from the pre-delta render pos toward the projected truth.
        const a = since / LERP_MS;
        v.rLon = v.lerpFromLon + (projLon - v.lerpFromLon) * a;
        v.rLat = v.lerpFromLat + (projLat - v.lerpFromLat) * a;
      } else {
        v.rLon = projLon;
        v.rLat = projLat;
      }
    }
    // Drop vessels we haven't received in a while: the server re-pushes every
    // in-view ship each tick, so a missing one has left the viewport (or aged
    // out). This keeps the rendered set ~= what's currently on screen.
    for (const [m, v] of this.vessels) {
      if (now - v.rx > VESSEL_TTL_MS) {
        this.vessels.delete(m);
        this.trails.delete(m);
      }
    }
    // Expire dark pings.
    if (this.pings.length) {
      this.pings = this.pings.filter((p) => now - p.born < PING_TTL_MS);
    }
  }

  // ── Reads for the render layer (called each frame from React) ────────────
  getRenderVessels(): RenderVessel[] {
    return Array.from(this.vessels.values());
  }

  getTrails(): { mmsi: number; path: [number, number][]; timestamps: number[] }[] {
    const out: { mmsi: number; path: [number, number][]; timestamps: number[] }[] = [];
    for (const [mmsi, pts] of this.trails) {
      if (pts.length < 2) continue;
      out.push({
        mmsi,
        path: pts.map((p) => [p.lon, p.lat] as [number, number]),
        timestamps: pts.map((p) => p.t),
      });
    }
    return out;
  }

  getPings(): DarkPing[] {
    return this.pings;
  }

  getVessel(mmsi: number): RenderVessel | undefined {
    return this.vessels.get(mmsi);
  }

  // Seed from a REST snapshot so the map isn't empty before the first WS tick.
  seed(vessels: VesselLite[]) {
    for (const v of vessels) this.applyDelta(v);
  }
}
