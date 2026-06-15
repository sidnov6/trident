"use client";

import { useEffect, useRef } from "react";
import { useStore } from "@/lib/store";
import { severityHex } from "@/lib/colors";

// Rolling per-zone congestion sparkline (z-score history) for the selected zone.
export default function CongestionStrip() {
  const selectedZone = useStore((s) => s.selectedZone);
  const zones = useStore((s) => s.zones);
  const z = zones[selectedZone];
  const histRef = useRef<Record<string, number[]>>({});

  // append latest z-score every render where it changed
  const hist = histRef.current;
  if (!hist[selectedZone]) hist[selectedZone] = [];
  useEffect(() => {
    if (!z) return;
    const arr = hist[selectedZone];
    arr.push(z.z);
    if (arr.length > 80) arr.shift();
  }, [z, selectedZone, hist]);

  const series = hist[selectedZone] ?? [];
  const w = 240;
  const h = 40;
  const max = Math.max(1, ...series.map((v) => Math.abs(v)));
  const bars = series.length;

  return (
    <div className="flex h-full items-center gap-3 border-r border-hairline px-3">
      <div className="flex flex-col">
        <span className="text-[10px] uppercase tracking-[0.2em] text-inkfaint">
          {selectedZone} congestion
        </span>
        <span className="tabular-nums text-[14px] font-bold text-ink">
          {z ? `z=${z.z.toFixed(2)}` : "—"}
          {z?.transit_min != null && (
            <span className="ml-2 text-[10px] text-inkdim">
              {z.transit_min}min transit
            </span>
          )}
        </span>
      </div>
      <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} className="bg-void">
        {series.map((v, i) => {
          const bw = w / Math.max(bars, 1);
          const bh = (Math.abs(v) / max) * (h - 2);
          const hex = severityHex(Math.min(1, Math.abs(v) / 3));
          return (
            <rect
              key={i}
              x={i * bw}
              y={h - bh}
              width={Math.max(1, bw - 1)}
              height={bh}
              fill={hex}
              opacity={0.85}
            />
          );
        })}
      </svg>
    </div>
  );
}
