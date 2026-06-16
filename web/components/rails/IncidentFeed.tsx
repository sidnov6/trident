"use client";

import { useStore } from "@/lib/store";
import { severityHex } from "@/lib/colors";
import { CHOKEPOINTS_BY_ID } from "@/lib/chokepoints";
import type { Incident } from "@/lib/contracts";

export default function IncidentFeed() {
  const incidents = useStore((s) => s.incidents);
  const selectedIncidentId = useStore((s) => s.selectedIncidentId);
  const selectIncident = useStore((s) => s.selectIncident);
  const requestFlyTo = useStore((s) => s.requestFlyTo);
  const openDossier = useStore((s) => s.openDossier);

  const onCard = (i: Incident) => {
    selectIncident(i.id);
    if (i.position) {
      requestFlyTo(i.position[1], i.position[0], 10.5);
    } else {
      const c = CHOKEPOINTS_BY_ID[i.zone];
      if (c) requestFlyTo(c.center[1], c.center[0], 9.5);
    }
  };

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-hairline bg-panel2 px-3 py-1.5">
        <span className="text-[11px] uppercase tracking-[0.2em] text-inkdim">
          Confirmed Cases
        </span>
        <span className="tabular-nums text-[10px] text-inkfaint">
          {incidents.length} OPEN
        </span>
      </div>

      <div className="max-h-[34vh] flex-1 overflow-y-auto">
        {incidents.length === 0 && (
          <div className="px-3 py-6 text-center text-[11px] text-inkfaint">
            <div className="animate-flicker">AWAITING CONFIRMED INCIDENTS</div>
            <div className="mt-1 text-inkfaint/70">
              cognition stream idle
            </div>
          </div>
        )}
        {incidents.map((i) => (
          <IncidentCard
            key={i.id}
            inc={i}
            selected={i.id === selectedIncidentId}
            onClick={() => onCard(i)}
            onOpen={() => openDossier(i.mmsi)}
          />
        ))}
      </div>
    </div>
  );
}

function IncidentCard({
  inc,
  selected,
  onClick,
  onOpen,
}: {
  inc: Incident;
  selected: boolean;
  onClick: () => void;
  onOpen: () => void;
}) {
  const hex = severityHex(inc.severity);
  const t = new Date(inc.opened_at * 1000);
  const ts = `${String(t.getUTCHours()).padStart(2, "0")}:${String(
    t.getUTCMinutes()
  ).padStart(2, "0")}Z`;

  return (
    <div
      onClick={onClick}
      className={`cursor-pointer border-b border-hairline px-3 py-2.5 transition-colors ${
        selected ? "bg-panel2" : "hover:bg-panel2/60"
      }`}
      style={selected ? { boxShadow: `inset 3px 0 0 ${hex}` } : undefined}
    >
      <div className="flex items-center justify-between">
        <span className="text-[12px] font-bold tracking-wide" style={{ color: hex }}>
          {inc.typology.replace(/_/g, " ")}
        </span>
        <span className="tabular-nums text-[10px] text-inkfaint">{ts}</span>
      </div>

      <div className="mt-0.5 flex items-center gap-2 text-[10px] text-inkdim">
        <span className="uppercase">{inc.zone}</span>
        <span className="text-inkfaint">·</span>
        <span className="tabular-nums">MMSI {inc.mmsi}</span>
        <span className="text-inkfaint">·</span>
        <span className="uppercase">{inc.status}</span>
      </div>

      {/* severity bar */}
      <div className="mt-1.5 h-1 w-full bg-hairline">
        <div
          className="h-full"
          style={{
            width: `${Math.round(inc.severity * 100)}%`,
            backgroundColor: hex,
            boxShadow: `0 0 6px ${hex}`,
          }}
        />
      </div>

      {inc.summary && (
        <p className="mt-1.5 line-clamp-3 text-[11px] leading-snug text-ink">
          {inc.analyst?.summary || inc.summary}
        </p>
      )}

      {(inc.desk?.market_note || inc.market_note) && (
        <p className="mt-1 line-clamp-2 border-l border-amberdim pl-2 text-[10px] italic leading-snug text-amber/80">
          {inc.desk?.market_note || inc.market_note}
        </p>
      )}

      <button
        onClick={(e) => {
          e.stopPropagation();
          onOpen();
        }}
        className="mt-2 border border-info/40 px-2 py-0.5 text-[10px] uppercase tracking-wider text-info hover:bg-info/10"
      >
        Open Case ›
      </button>
    </div>
  );
}
