"use client";

import { useStore } from "@/lib/store";
import type { SignalLite } from "@/lib/contracts";

function fmt(s: SignalLite): string {
  const d = new Date(s.ts * 1000);
  const ts = d.toISOString().slice(11, 19);
  const mmsi = String(s.mmsi);
  return `${ts}Z · ${s.zone.toUpperCase()} · ${s.type} · MMSI ${mmsi}`;
}

function color(t: SignalLite["type"]): string {
  if (t === "DARK_VESSEL" || t === "GEOFENCE_BREACH" || t === "IDENTITY_CONFLICT")
    return "var(--alert)";
  if (t === "REROUTE" || t === "CONGESTION") return "var(--amber)";
  return "var(--info)";
}

export default function SignalTicker() {
  const signals = useStore((s) => s.signals);
  const row = signals.slice(0, 40);

  return (
    <div className="flex h-6 items-center overflow-hidden border-t border-hairline bg-void">
      <span className="shrink-0 border-r border-hairline bg-panel px-2 text-[10px] uppercase tracking-[0.25em] text-amber">
        DETECTOR FEED
      </span>
      <div className="ticker-mask relative flex-1 overflow-hidden">
        {row.length === 0 ? (
          <span className="px-3 text-[11px] text-inkfaint animate-flicker">
            no detector firings — pipeline idle
          </span>
        ) : (
          <div className="ticker-track flex items-center whitespace-nowrap">
            {[...row, ...row].map((s, i) => (
              <span
                key={`${s.id}-${i}`}
                className="px-4 text-[11px] tabular-nums"
                style={{ color: color(s.type) }}
              >
                {fmt(s)}
                <span className="ml-4 text-inkfaint">|</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
