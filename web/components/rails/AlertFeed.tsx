"use client";

import { useState } from "react";
import { useStore } from "@/lib/store";
import { THREAT_CATEGORY, type ThreatCategory, type FleetAlert } from "@/lib/contracts";

const ALL_CATS = Object.keys(THREAT_CATEGORY) as ThreatCategory[];

function ago(ts: number): string {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function band(sev: number): { word: string; color: string } {
  if (sev >= 0.8) return { word: "Severe", color: "#D00000" };
  if (sev >= 0.6) return { word: "High", color: "#F48C06" };
  if (sev >= 0.35) return { word: "Watch", color: "#FB8500" };
  return { word: "Low", color: "#52B788" };
}

export default function AlertFeed() {
  const alerts = useStore((s) => s.alerts);
  const counts = useStore((s) => s.alertCounts);
  const muted = useStore((s) => s.mutedCategories);
  const toggleCategory = useStore((s) => s.toggleCategory);
  const requestFlyTo = useStore((s) => s.requestFlyTo);
  const openDossier = useStore((s) => s.openDossier);
  const selectIncident = useStore((s) => s.selectIncident);
  const [bySeverity, setBySeverity] = useState(false);

  const visible = alerts.filter((a) => !muted[a.category]);
  const shown = bySeverity
    ? [...visible].sort((a, b) => b.risk - a.risk)
    : visible;

  function investigate(a: FleetAlert) {
    const [lat, lon] = a.position;
    requestFlyTo(lon, lat, 8.5);
    openDossier(a.mmsi);
    selectIncident(null);
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between border-b border-hairline px-3 py-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.2em] text-ink">
          Live Threats
        </span>
        <button
          onClick={() => setBySeverity((v) => !v)}
          className="text-[9px] uppercase tracking-wider text-inkdim hover:text-ink"
        >
          {bySeverity ? "Newest" : "By risk"}
        </button>
      </div>

      {/* category filter chips */}
      <div className="flex flex-wrap gap-1 border-b border-hairline px-2 py-2">
        {ALL_CATS.map((c) => {
          const meta = THREAT_CATEGORY[c];
          const n = counts[c] ?? 0;
          const off = muted[c];
          return (
            <button
              key={c}
              onClick={() => toggleCategory(c)}
              title={meta.blurb}
              className={`flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[9px] uppercase tracking-wide transition-opacity ${
                off ? "opacity-35" : "opacity-100"
              }`}
              style={{ borderColor: meta.color, color: meta.color }}
            >
              <span
                className="inline-block h-1.5 w-1.5 rounded-full"
                style={{ background: meta.color }}
              />
              {meta.label}
              {n > 0 && <span className="font-mono text-ink">{n}</span>}
            </button>
          );
        })}
      </div>

      {/* alert cards */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {shown.length === 0 && (
          <div className="px-3 py-6 text-center text-[11px] text-inkfaint">
            No threats flagged right now. The agents are watching every ship
            worldwide — alerts appear here the moment one behaves dangerously.
          </div>
        )}
        {shown.map((a) => {
          const meta = THREAT_CATEGORY[a.category];
          const b = band(a.severity);
          return (
            <button
              key={a.id}
              onClick={() => investigate(a)}
              className="block w-full border-b border-hairline px-3 py-2 text-left transition-colors hover:bg-panel2"
              style={{ borderLeft: `3px solid ${meta.color}` }}
            >
              <div className="flex items-center justify-between gap-2">
                <span
                  className="rounded-sm px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-white"
                  style={{ background: meta.color }}
                >
                  {meta.label}
                </span>
                <span
                  className="text-[9px] font-semibold uppercase tracking-wide"
                  style={{ color: b.color }}
                >
                  {b.word}
                </span>
              </div>
              <div className="mt-1 truncate text-[12px] font-medium text-ink">
                {a.name || `Ship ${a.mmsi}`}
                {a.flag && (
                  <span className="ml-1 text-[10px] font-normal text-inkdim">· {a.flag}</span>
                )}
              </div>
              {(a.narrative || a.evidence[0]) && (
                <div className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-inkdim">
                  {a.narrative || a.evidence[0]}
                </div>
              )}
              <div className="mt-1 flex items-center justify-between text-[9px] uppercase tracking-wider text-inkfaint">
                <span>Track ship ›</span>
                <span>{ago(a.ts)}</span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
