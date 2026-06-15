"use client";

import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import { MapboxOverlay } from "@deck.gl/mapbox";
import "maplibre-gl/dist/maplibre-gl.css";

import { TridentFeed } from "@/lib/ws";
import { buildLayers } from "./layers";
import { loadGeo, type FeatureLike } from "./geo";
import { useStore } from "@/lib/store";
import { getVessels, getZones, getHealth, normalizeZones } from "@/lib/api";
import { DEFAULT_CENTER, DEFAULT_ZOOM } from "@/lib/chokepoints";
import { darkStyle } from "./style";

// WS endpoint resolution:
//  1. explicit NEXT_PUBLIC_WS_URL wins;
//  2. same-origin mode (NEXT_PUBLIC_API_BASE === "") → derive ws(s)://<this host>/ws
//     so the single-container HF Space connects back to its own origin;
//  3. otherwise localhost dev default.
function resolveWsUrl(): string {
  const explicit = process.env.NEXT_PUBLIC_WS_URL;
  if (explicit) return explicit;
  if (process.env.NEXT_PUBLIC_API_BASE === "" && typeof window !== "undefined") {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${window.location.host}/ws`;
  }
  return "ws://localhost:8000/ws";
}

export default function MapView() {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const overlayRef = useRef<MapboxOverlay | null>(null);
  const feedRef = useRef<TridentFeed | null>(null);
  const geoRef = useRef<{
    fairway?: FeatureLike;
    exclusion?: FeatureLike;
    anchorage?: FeatureLike;
  }>({});
  const zoomRef = useRef(DEFAULT_ZOOM);
  const [ready, setReady] = useState(false);

  // msgs/sec metering
  const msgWindow = useRef<number[]>([]);

  const setHealth = useStore((s) => s.setHealth);
  const pushIncident = useStore((s) => s.pushIncident);
  const pushSignal = useStore((s) => s.pushSignal);
  const setZone = useStore((s) => s.setZone);
  const setZones = useStore((s) => s.setZones);
  const setVesselCount = useStore((s) => s.setVesselCount);
  const openDossier = useStore((s) => s.openDossier);

  // ── init map + overlay + feed (once) ──────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: darkStyle(),
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      attributionControl: false,
      antialias: true,
    });
    mapRef.current = map;

    map.on("load", () => {
      // OpenSeaMap seamark overlay
      if (!map.getSource("seamark")) {
        map.addSource("seamark", {
          type: "raster",
          tiles: ["https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png"],
          tileSize: 256,
          minzoom: 9,
          maxzoom: 18,
        });
        map.addLayer({
          id: "seamark",
          type: "raster",
          source: "seamark",
          minzoom: 9,
          paint: { "raster-opacity": 0.7 },
        });
      }

      const overlay = new MapboxOverlay({ interleaved: true, layers: [] });
      overlayRef.current = overlay;
      map.addControl(overlay as unknown as maplibregl.IControl);
      setReady(true);
    });

    map.on("zoom", () => {
      zoomRef.current = map.getZoom();
    });

    // load geo outlines
    Promise.all([
      loadGeo("suez_fairway"),
      loadGeo("suez_exclusion"),
      loadGeo("suez_anchorage"),
    ]).then(([fairway, exclusion, anchorage]) => {
      geoRef.current = { fairway, exclusion, anchorage };
    });

    // live feed
    const feed = new TridentFeed(resolveWsUrl(), {
      onStatus: (online) =>
        setHealth({
          online,
          msgPerSec: useStore.getState().health.msgPerSec,
          lastMsgMs: Date.now(),
        }),
      onMsg: () => msgWindow.current.push(Date.now()),
      onIncident: (i) => pushIncident(i),
      onSignal: (s) => pushSignal(s),
      onZoneStats: (z) => setZone(z),
    });
    feedRef.current = feed;
    feed.start();

    // REST seed (defensive — empty if offline)
    getVessels("suez").then((vs) => {
      feed.seed(vs);
      setVesselCount(vs.length);
    });
    getZones().then((zs) => {
      if (zs.length) setZones(zs);
    });
    getHealth().then((h) => setHealth(h));

    // health poll
    const healthTimer = setInterval(async () => {
      const h = await getHealth();
      // measured msgs/sec from the WS window
      const now = Date.now();
      msgWindow.current = msgWindow.current.filter((t) => now - t < 1000);
      const measured = msgWindow.current.length;
      setHealth({
        online: h.online || measured > 0,
        msgPerSec: measured || h.msgPerSec,
        lastMsgMs: now,
      });
    }, 1000);

    return () => {
      clearInterval(healthTimer);
      feed.stop();
      map.remove();
      mapRef.current = null;
      overlayRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── fly-to from store ─────────────────────────────────────────────────────
  const flyTo = useStore((s) => s.flyTo);
  useEffect(() => {
    if (!flyTo || !mapRef.current) return;
    mapRef.current.flyTo({
      center: [flyTo.lon, flyTo.lat],
      zoom: flyTo.zoom ?? 9.5,
      speed: 1.2,
      curve: 1.4,
      essential: true,
    });
  }, [flyTo]);

  // ── render loop: rebuild deck layers each frame ───────────────────────────
  const selectedIncidentId = useStore((s) => s.selectedIncidentId);
  const incidents = useStore((s) => s.incidents);
  const zones = useStore((s) => s.zones);
  useEffect(() => {
    if (!ready) return;
    let raf = 0;
    const tick = () => {
      const feed = feedRef.current;
      const overlay = overlayRef.current;
      if (feed && overlay) {
        const now = Date.now();
        const vessels = feed.getRenderVessels();
        const selInc = incidents.find((i) => i.id === selectedIncidentId);
        const layers = buildLayers({
          vessels,
          trails: feed.getTrails(),
          pings: feed.getPings(),
          zones,
          incidents,
          geo: geoRef.current,
          nowMs: now,
          zoom: zoomRef.current,
          selectedMmsi: selInc ? selInc.mmsi : null,
          onVesselClick: (mmsi) => openDossier(mmsi),
        });
        overlay.setProps({ layers });
        setVesselCount(vessels.length);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [ready, incidents, zones, selectedIncidentId, openDossier, setVesselCount]);

  const online = useStore((s) => s.health.online);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="absolute inset-0" />
      {/* ops-room overlays */}
      <div className="pointer-events-none absolute inset-0 z-[5] vignette" />
      <div className="pointer-events-none absolute inset-0 z-[5] scanlines" />
      {!online && (
        <div className="pointer-events-none absolute left-1/2 top-4 z-10 -translate-x-1/2">
          <div className="border border-alert/60 bg-void/80 px-3 py-1 text-[11px] uppercase tracking-[0.3em] text-alert shadow-glowalert animate-flicker">
            ● Feed Offline
          </div>
        </div>
      )}
    </div>
  );
}
