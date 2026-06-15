"use client";

import { useEffect, useState } from "react";
import { useStore } from "@/lib/store";
import { CHOKEPOINTS } from "@/lib/chokepoints";
import { THREAT_HEX } from "@/lib/colors";
import type { ThreatLevel } from "@/lib/contracts";

function useUtcClock(): string {
  const [t, setT] = useState("--:--:--");
  useEffect(() => {
    const fmt = () => {
      const d = new Date();
      const p = (n: number) => String(n).padStart(2, "0");
      setT(
        `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}Z`
      );
    };
    fmt();
    const id = setInterval(fmt, 1000);
    return () => clearInterval(id);
  }, []);
  return t;
}

export default function StatusBar() {
  const clock = useUtcClock();
  const health = useStore((s) => s.health);
  const zones = useStore((s) => s.zones);

  const dotColor = !health.online
    ? "var(--alert)"
    : health.msgPerSec > 0
    ? "var(--green)"
    : "var(--elevated)";

  return (
    <header className="flex h-9 shrink-0 items-center gap-4 border-b border-hairline bg-panel px-3 text-[11px] uppercase tracking-wider">
      <div className="flex items-center gap-2">
        <span className="text-amber font-bold tracking-[0.35em]">TRIDENT</span>
        <span className="text-inkfaint">/ CHOKEPOINT INTEL</span>
      </div>

      <div className="ml-1 tabular-nums text-ink">{clock}</div>

      <div className="flex items-center gap-2">
        <span
          className="inline-block h-2.5 w-2.5 rounded-full"
          style={{ backgroundColor: dotColor, boxShadow: `0 0 8px ${dotColor}` }}
        />
        <span className="text-inkdim">
          {health.online ? "FEED LIVE" : "FEED OFFLINE"}
        </span>
        <span className="tabular-nums text-ink">
          {health.msgPerSec.toFixed(0)}
        </span>
        <span className="text-inkfaint">msg/s</span>
      </div>

      {/* threat strip */}
      <div className="ml-auto flex items-center gap-1.5">
        {CHOKEPOINTS.map((c) => {
          const z = zones[c.id];
          const level: ThreatLevel = z?.threat_level ?? "GREEN";
          const hex = THREAT_HEX[level];
          return (
            <div
              key={c.id}
              title={`${c.name} — ${level}`}
              className="flex items-center gap-1 border border-hairline px-1.5 py-0.5"
            >
              <span
                className="inline-block h-2 w-2"
                style={{ backgroundColor: hex, boxShadow: `0 0 6px ${hex}` }}
              />
              <span className="text-[10px] text-inkdim">
                {c.id.slice(0, 4).toUpperCase()}
              </span>
            </div>
          );
        })}
      </div>
    </header>
  );
}
