"use client";

import { useStore } from "@/lib/store";
import { CHOKEPOINTS } from "@/lib/chokepoints";
import { THREAT_HEX } from "@/lib/colors";
import type { ThreatLevel, SignalType } from "@/lib/contracts";
import RegionPanel from "./RegionPanel";

const SIGNAL_TYPES: SignalType[] = [
  "DARK_VESSEL",
  "LOITERING",
  "POSITION_JUMP",
  "IDENTITY_CONFLICT",
  "CONGESTION",
  "GEOFENCE_BREACH",
  "REROUTE",
  "UTURN",
];

export default function LeftRail() {
  const selectedZone = useStore((s) => s.selectedZone);
  const setSelectedZone = useStore((s) => s.setSelectedZone);
  const requestFlyTo = useStore((s) => s.requestFlyTo);
  const zones = useStore((s) => s.zones);
  const vesselCount = useStore((s) => s.vesselCount);
  const signalCounts = useStore((s) => s.signalCounts);

  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-hairline bg-panel">
      <SectionLabel>Chokepoints</SectionLabel>
      <div className="flex flex-col">
        {CHOKEPOINTS.map((c) => {
          const z = zones[c.id];
          const level: ThreatLevel = z?.threat_level ?? "GREEN";
          const active = selectedZone === c.id;
          return (
            <button
              key={c.id}
              onClick={() => {
                setSelectedZone(c.id);
                requestFlyTo(c.center[1], c.center[0], 9.2);
              }}
              className={`group flex items-center justify-between px-3 py-2 text-left text-[12px] transition-colors ${
                active
                  ? "bg-panel2 text-amber"
                  : "text-inkdim hover:bg-panel2 hover:text-ink"
              }`}
            >
              <span className="flex items-center gap-2">
                <span
                  className="inline-block h-2 w-2"
                  style={{
                    backgroundColor: THREAT_HEX[level],
                    boxShadow: `0 0 6px ${THREAT_HEX[level]}`,
                  }}
                />
                <span className="truncate">{c.name}</span>
              </span>
              <span className="tabular-nums text-[10px] text-inkfaint">
                {z?.count ?? 0}
              </span>
            </button>
          );
        })}
      </div>

      <SectionLabel>Live Vessels</SectionLabel>
      <div className="px-3 py-2">
        <div className="font-bold tabular-nums text-[28px] leading-none text-info">
          {vesselCount}
        </div>
        <div className="mt-1 text-[10px] uppercase tracking-wider text-inkfaint">
          tracked in viewport
        </div>
      </div>

      <SectionLabel>Active Signals</SectionLabel>
      <div className="flex flex-col gap-px overflow-y-auto px-1 pb-2 text-[11px]">
        {SIGNAL_TYPES.map((t) => {
          const n = signalCounts[t] ?? 0;
          return (
            <div
              key={t}
              className="flex items-center justify-between px-2 py-1"
            >
              <span
                className={
                  t === "DARK_VESSEL"
                    ? "text-alert"
                    : n > 0
                    ? "text-ink"
                    : "text-inkfaint"
                }
              >
                {t.replace("_", " ")}
              </span>
              <span className="tabular-nums text-inkdim">{n}</span>
            </div>
          );
        })}
      </div>

      <div className="mt-auto">
        <RegionPanel />
      </div>
    </aside>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="border-y border-hairline bg-void px-3 py-1 text-[10px] uppercase tracking-[0.25em] text-inkfaint">
      {children}
    </div>
  );
}
