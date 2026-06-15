"use client";

import { useState } from "react";
import { useStore } from "@/lib/store";
import { analyzeRegion, type RegionAnalysis } from "@/lib/api";
import { SHIP_LABEL, shipColor } from "@/lib/colors";
import { SHIP_BUCKET } from "@/lib/contracts";

// Bucket order for the legend (skip OTHER unless present).
const ORDER = [
  SHIP_BUCKET.TANKER,
  SHIP_BUCKET.CARGO,
  SHIP_BUCKET.PASSENGER,
  SHIP_BUCKET.FISHING,
  SHIP_BUCKET.HIGH_SPEED,
  SHIP_BUCKET.TUG_SPECIAL,
  SHIP_BUCKET.OTHER,
];

function rgba(c: [number, number, number, number]): string {
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

export default function RegionPanel() {
  const buckets = useStore((s) => s.viewportBuckets);
  const bbox = useStore((s) => s.viewportBbox);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RegionAnalysis | null>(null);

  const total = buckets.reduce((a, b) => a + b, 0);
  const max = Math.max(1, ...buckets);

  async function onAnalyze() {
    if (!bbox || busy) return;
    setBusy(true);
    setResult(null);
    const r = await analyzeRegion(bbox);
    setResult(r);
    setBusy(false);
  }

  return (
    <div className="border-t border-hairline px-3 py-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-[0.25em] text-inkdim">
          Traffic in view
        </span>
        <span className="font-mono text-[11px] text-inkdim">{total}</span>
      </div>

      <div className="space-y-1">
        {ORDER.filter((b) => buckets[b] > 0).map((b) => (
          <div key={b} className="flex items-center gap-2">
            <span
              className="inline-block h-2 w-2 shrink-0 rounded-sm"
              style={{ background: rgba(shipColor(b)) }}
            />
            <span className="w-20 shrink-0 text-[10px] uppercase tracking-wide text-inkdim">
              {SHIP_LABEL[b]}
            </span>
            <span className="relative h-1.5 flex-1 overflow-hidden rounded-sm bg-panel2">
              <span
                className="absolute inset-y-0 left-0 rounded-sm"
                style={{
                  width: `${(buckets[b] / max) * 100}%`,
                  background: rgba(shipColor(b)),
                  opacity: 0.7,
                }}
              />
            </span>
            <span className="w-8 shrink-0 text-right font-mono text-[11px] text-ink">
              {buckets[b]}
            </span>
          </div>
        ))}
        {total === 0 && (
          <div className="text-[11px] text-inkfaint">No vessels in view.</div>
        )}
      </div>

      <button
        onClick={onAnalyze}
        disabled={!bbox || busy || total === 0}
        className="mt-3 w-full rounded-sm border border-amber/40 bg-amber/5 px-2 py-1.5 text-[10px] font-semibold uppercase tracking-[0.2em] text-amber transition-colors hover:bg-amber/10 disabled:opacity-40"
      >
        {busy ? "Analyzing region…" : "Analyze region (agent)"}
      </button>

      {result && (
        <div className="mt-2 rounded-sm border border-hairline bg-panel2 p-2">
          <p className="text-[11px] leading-relaxed text-ink">{result.analysis}</p>
          {result.model && (
            <p className="mt-1 text-[9px] uppercase tracking-wider text-inkfaint">
              {result.model === "deterministic-fallback"
                ? "rule-based"
                : `agent · ${result.model}`}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
