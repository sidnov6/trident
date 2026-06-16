"use client";

import { useEffect, useState } from "react";
import { useStore } from "@/lib/store";
import { getDossier } from "@/lib/api";
import { SHIP_LABEL } from "@/lib/colors";
import { THREAT_CATEGORY, type VesselDossier, type ThreatCategory } from "@/lib/contracts";

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toISOString().slice(11, 16) + "Z";
}
function fmtAgo(ts?: number | null): string {
  if (!ts) return "—";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export default function VesselDossierPanel() {
  const mmsi = useStore((s) => s.dossierMmsi);
  const clearSelectedVessel = useStore((s) => s.clearSelectedVessel);
  const setSelectedTrack = useStore((s) => s.setSelectedTrack);
  const [dossier, setDossier] = useState<VesselDossier | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (mmsi == null) {
      setDossier(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    getDossier(mmsi).then((d) => {
      if (cancelled) return;
      setDossier(d);
      setLoading(false);
      // hand the traveled path to the map so it draws the route + heading
      if (d && d.track.length >= 2) setSelectedTrack(d.track);
    });
    return () => {
      cancelled = true;
    };
  }, [mmsi, setSelectedTrack]);

  const open = mmsi != null;
  const watch = dossier?.watch_category as ThreatCategory | undefined;

  return (
    <div
      className={`pointer-events-none absolute right-0 top-0 z-20 h-full w-[360px] transform border-l border-hairline bg-panel/97 shadow-lg backdrop-blur-sm transition-transform duration-300 ${
        open ? "translate-x-0 pointer-events-auto" : "translate-x-full"
      }`}
    >
      <div className="flex items-center justify-between border-b border-hairline bg-panel2 px-3 py-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.2em] text-ink">
          Ship Details
        </span>
        <button
          onClick={clearSelectedVessel}
          className="rounded-sm border border-hairline px-2 text-[12px] text-inkdim hover:text-alert"
        >
          ✕
        </button>
      </div>

      <div className="h-[calc(100%-40px)] overflow-y-auto px-3 py-3 text-[12px]">
        {loading && <div className="text-inkfaint">Looking up this ship…</div>}
        {!loading && !dossier && (
          <div className="text-inkfaint">
            <div className="font-semibold text-alert">No record yet</div>
            <div className="mt-1">ID {mmsi}</div>
            <div className="mt-2 text-[11px] leading-snug">
              We haven&apos;t collected details for this ship yet.
            </div>
          </div>
        )}
        {dossier && (
          <>
            <div className="text-[18px] font-bold leading-tight text-ink">
              {dossier.name || `Ship ${dossier.mmsi}`}
            </div>
            <div className="mb-3 mt-0.5 flex flex-wrap gap-1">
              {dossier.flag && (
                <span className="rounded-sm bg-panel2 px-1.5 py-0.5 text-[10px] text-inkdim">
                  {dossier.flag}
                </span>
              )}
              {dossier.flag_of_convenience && (
                <span className="rounded-sm bg-amber/10 px-1.5 py-0.5 text-[10px] text-amber">
                  flag of convenience
                </span>
              )}
              {watch && THREAT_CATEGORY[watch] && (
                <span
                  className="rounded-sm px-1.5 py-0.5 text-[10px] font-semibold text-white"
                  style={{ background: THREAT_CATEGORY[watch].color }}
                >
                  {THREAT_CATEGORY[watch].label}
                </span>
              )}
            </div>

            {dossier.watch_reason && (
              <div className="mb-3 rounded-sm border border-hairline bg-panel2 p-2 text-[11px] leading-snug text-ink">
                ⚠ {dossier.watch_reason}
              </div>
            )}

            {/* where it's going */}
            <SubLabel>Right now</SubLabel>
            <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
              <Field
                k="Heading"
                v={
                  dossier.course_compass
                    ? `${dossier.course_compass} (${Math.round(dossier.cog ?? 0)}°)`
                    : "—"
                }
              />
              <Field
                k="Speed"
                v={dossier.sog != null ? `${dossier.sog.toFixed(1)} kn` : "—"}
              />
              <Field k="Last seen" v={fmtAgo(dossier.last_fix_ts)} />
              <Field k="Going to" v={dossier.destination || "Not stated"} />
            </dl>

            {/* where it came from + the route */}
            <SubLabel>Where it&apos;s been</SubLabel>
            {dossier.origin ? (
              <div className="text-[11px] text-inkdim">
                Tracked from{" "}
                <span className="text-ink">
                  {dossier.origin[1].toFixed(2)}°, {dossier.origin[2].toFixed(2)}°
                </span>{" "}
                · {fmtTime(dossier.origin[0])} · {dossier.track.length} points
              </div>
            ) : (
              <div className="text-[11px] text-inkfaint">
                Tracking this ship from now — its route will fill in.
              </div>
            )}
            <TrackSpark track={dossier.track} />

            {/* identity */}
            <SubLabel>Identity</SubLabel>
            <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
              <Field k="ID (MMSI)" v={dossier.mmsi} />
              <Field k="IMO" v={dossier.imo ?? "—"} />
              <Field
                k="Type"
                v={dossier.ship_type != null ? SHIP_LABEL[dossier.ship_type] ?? "Other" : "Not broadcast"}
              />
              <Field k="Length" v={dossier.length != null ? `${dossier.length} m` : "—"} />
            </dl>

            {dossier.incident_ids.length > 0 && (
              <>
                <SubLabel>Past cases</SubLabel>
                <div className="text-[11px] text-inkdim">
                  {dossier.incident_ids.length} confirmed incident(s) on record
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Field({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex flex-col border-b border-hairline/60 pb-1">
      <dt className="text-[9px] uppercase tracking-wider text-inkfaint">{k}</dt>
      <dd className="tabular-nums text-ink">{v}</dd>
    </div>
  );
}

function SubLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-1 mt-4 text-[10px] font-semibold uppercase tracking-[0.2em] text-inkdim">
      {children}
    </div>
  );
}

// Tiny inline sparkline of the track with start (green) + current (red) markers.
function TrackSpark({ track }: { track: [number, number, number][] }) {
  if (track.length < 2) return null;
  const lats = track.map((p) => p[1]);
  const lons = track.map((p) => p[2]);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const minLon = Math.min(...lons), maxLon = Math.max(...lons);
  const w = 320, h = 90, pad = 8;
  const sx = (lon: number) => pad + ((lon - minLon) / (maxLon - minLon || 1)) * (w - 2 * pad);
  const sy = (lat: number) => h - pad - ((lat - minLat) / (maxLat - minLat || 1)) * (h - 2 * pad);
  const d = track
    .map((p, i) => `${i === 0 ? "M" : "L"}${sx(p[2]).toFixed(1)},${sy(p[1]).toFixed(1)}`)
    .join(" ");
  return (
    <svg width={w} height={h} className="my-1 rounded-sm border border-hairline bg-panel2" viewBox={`0 0 ${w} ${h}`}>
      <path d={d} fill="none" stroke="#1f5fbf" strokeWidth={1.4} opacity={0.9} />
      <circle cx={sx(lons[0])} cy={sy(lats[0])} r={3} fill="#16a34a" />
      <circle cx={sx(lons[lons.length - 1])} cy={sy(lats[lats.length - 1])} r={3.5} fill="#dc2626" />
    </svg>
  );
}
