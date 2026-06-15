"use client";

import { useEffect, useState } from "react";
import { useStore } from "@/lib/store";
import { getDossier } from "@/lib/api";
import { SHIP_LABEL } from "@/lib/colors";
import type { VesselDossier } from "@/lib/contracts";

export default function VesselDossierPanel() {
  const mmsi = useStore((s) => s.dossierMmsi);
  const openDossier = useStore((s) => s.openDossier);
  const requestFlyTo = useStore((s) => s.requestFlyTo);
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
      if (!cancelled) {
        setDossier(d);
        setLoading(false);
        if (d && d.track.length) {
          const last = d.track[d.track.length - 1];
          requestFlyTo(last[2], last[1], 11);
        }
      }
    });
    return () => {
      cancelled = true;
    };
  }, [mmsi, requestFlyTo]);

  const open = mmsi != null;

  return (
    <div
      className={`pointer-events-none absolute right-0 top-0 z-20 h-full w-[360px] transform border-l border-hairline bg-panel/95 backdrop-blur-sm transition-transform duration-300 ${
        open ? "translate-x-0 pointer-events-auto" : "translate-x-full"
      }`}
    >
      <div className="flex items-center justify-between border-b border-hairline bg-void px-3 py-2">
        <span className="text-[11px] uppercase tracking-[0.25em] text-amber">
          Vessel Dossier
        </span>
        <button
          onClick={() => openDossier(null)}
          className="border border-hairline px-2 text-[12px] text-inkdim hover:text-alert"
        >
          ✕
        </button>
      </div>

      <div className="h-[calc(100%-36px)] overflow-y-auto px-3 py-3 text-[12px]">
        {loading && (
          <div className="animate-flicker text-inkfaint">QUERYING REGISTRY…</div>
        )}
        {!loading && !dossier && (
          <div className="text-inkfaint">
            <div className="text-alert">NO REGISTRY RECORD</div>
            <div className="mt-1">MMSI {mmsi}</div>
            <div className="mt-2 text-[11px] leading-snug">
              Dossier endpoint unavailable or vessel not yet persisted.
            </div>
          </div>
        )}
        {dossier && (
          <>
            <div className="mb-2 text-[18px] font-bold leading-tight text-ink">
              {dossier.name || `MMSI ${dossier.mmsi}`}
            </div>
            <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
              <Field k="MMSI" v={dossier.mmsi} />
              <Field k="IMO" v={dossier.imo ?? "—"} />
              <Field k="FLAG" v={dossier.flag ?? "—"} />
              <Field
                k="TYPE"
                v={
                  dossier.ship_type != null
                    ? SHIP_LABEL[dossier.ship_type] ?? dossier.ship_type
                    : "—"
                }
              />
              <Field
                k="DRAUGHT"
                v={dossier.draught != null ? `${dossier.draught} m` : "—"}
              />
              <Field
                k="LOA"
                v={dossier.length != null ? `${dossier.length} m` : "—"}
              />
              <Field k="DEST" v={dossier.destination ?? "—"} />
              <Field
                k="BEAM"
                v={dossier.beam != null ? `${dossier.beam} m` : "—"}
              />
            </dl>

            <SubLabel>Track</SubLabel>
            <div className="text-[11px] text-inkdim">
              {dossier.track.length} fixes
              {dossier.track.length > 0 && (
                <span className="text-inkfaint">
                  {" "}
                  · last{" "}
                  {new Date(
                    dossier.track[dossier.track.length - 1][0] * 1000
                  )
                    .toISOString()
                    .slice(11, 19)}
                  Z
                </span>
              )}
            </div>
            <TrackSpark track={dossier.track} />

            <SubLabel>Incident History</SubLabel>
            {dossier.incident_ids.length === 0 ? (
              <div className="text-[11px] text-inkfaint">no prior incidents</div>
            ) : (
              <ul className="space-y-0.5">
                {dossier.incident_ids.map((id) => (
                  <li
                    key={id}
                    className="truncate border-l border-amberdim pl-2 text-[10px] text-amber/80"
                  >
                    {id}
                  </li>
                ))}
              </ul>
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
    <div className="mb-1 mt-4 text-[10px] uppercase tracking-[0.25em] text-inkfaint">
      {children}
    </div>
  );
}

// Tiny inline sparkline of the track lon/lat.
function TrackSpark({ track }: { track: [number, number, number][] }) {
  if (track.length < 2) return null;
  const lats = track.map((p) => p[1]);
  const lons = track.map((p) => p[2]);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);
  const w = 320;
  const h = 90;
  const pad = 6;
  const sx = (lon: number) =>
    pad + ((lon - minLon) / (maxLon - minLon || 1)) * (w - 2 * pad);
  const sy = (lat: number) =>
    h - pad - ((lat - minLat) / (maxLat - minLat || 1)) * (h - 2 * pad);
  const d = track
    .map((p, i) => `${i === 0 ? "M" : "L"}${sx(p[2]).toFixed(1)},${sy(p[1]).toFixed(1)}`)
    .join(" ");
  return (
    <svg
      width={w}
      height={h}
      className="my-1 border border-hairline bg-void"
      viewBox={`0 0 ${w} ${h}`}
    >
      <path d={d} fill="none" stroke="#2ee6ff" strokeWidth={1.2} opacity={0.9} />
      <circle cx={sx(lons[lons.length - 1])} cy={sy(lats[lats.length - 1])} r={3} fill="#ffb000" />
    </svg>
  );
}
