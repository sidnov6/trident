"use client";

import { useStore } from "@/lib/store";
import { THREAT_CATEGORY, type FleetAlert } from "@/lib/contracts";

function headline(a: FleetAlert): string {
  const meta = THREAT_CATEGORY[a.category];
  const who = a.name || `Ship ${a.mmsi}`;
  const why = a.evidence[0] ? ` — ${a.evidence[0]}` : "";
  return `${meta.label.toUpperCase()}: ${who}${why}`;
}

export default function AlertTicker() {
  const alerts = useStore((s) => s.alerts);
  const muted = useStore((s) => s.mutedCategories);
  const requestFlyTo = useStore((s) => s.requestFlyTo);
  const openDossier = useStore((s) => s.openDossier);
  const row = alerts.filter((a) => !muted[a.category]).slice(0, 30);

  return (
    <div className="flex h-7 items-center overflow-hidden border-t border-hairline bg-panel">
      <span className="shrink-0 border-r border-hairline bg-alert/10 px-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-alert">
        ⚠ Live Threats
      </span>
      <div className="ticker-mask relative flex-1 overflow-hidden">
        {row.length === 0 ? (
          <span className="px-3 text-[11px] text-inkfaint">
            All clear — no ships flagged right now
          </span>
        ) : (
          <div className="ticker-track flex items-center whitespace-nowrap">
            {[...row, ...row].map((a, i) => (
              <button
                key={`${a.id}-${i}`}
                onClick={() => {
                  const [lat, lon] = a.position;
                  requestFlyTo(lon, lat, 8.5);
                  openDossier(a.mmsi);
                }}
                className="px-4 text-[11px] font-medium hover:underline"
                style={{ color: THREAT_CATEGORY[a.category].color }}
              >
                {headline(a)}
                <span className="ml-4 text-inkfaint">|</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
