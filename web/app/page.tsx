"use client";

import dynamic from "next/dynamic";
import StatusBar from "@/components/statusbar/StatusBar";
import LeftRail from "@/components/rails/LeftRail";
import IncidentFeed from "@/components/rails/IncidentFeed";
import AlertFeed from "@/components/rails/AlertFeed";
import VesselDossierPanel from "@/components/rails/VesselDossier";
import SignalTicker from "@/components/ticker/SignalTicker";
import AlertTicker from "@/components/ticker/AlertTicker";
import CongestionStrip from "@/components/ticker/CongestionStrip";
import ReplayScrubber from "@/components/replay/ReplayScrubber";

// WebGL map is client-only.
const MapView = dynamic(() => import("@/components/map/Map"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center bg-void">
      <span className="animate-flicker text-[11px] uppercase tracking-[0.3em] text-amber">
        Initializing chart engine…
      </span>
    </div>
  ),
});

export default function CommandCenter() {
  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-void">
      <StatusBar />

      <div className="flex min-h-0 flex-1">
        <LeftRail />

        {/* center: map (most pixels). overflow-hidden clips the dossier slide-over
            to the map so, when closed, it doesn't bleed over the right rail. */}
        <main className="relative min-w-0 flex-1 overflow-hidden">
          <MapView />
          <VesselDossierPanel />
        </main>

        {/* right rail: live threats (primary) over confirmed cases */}
        <aside className="flex w-[340px] shrink-0 flex-col border-l border-hairline bg-panel">
          <AlertFeed />
          <IncidentFeed />
        </aside>
      </div>

      {/* bottom strip */}
      <div className="flex h-12 shrink-0 items-stretch border-t border-hairline bg-panel">
        <CongestionStrip />
        <ReplayScrubber />
      </div>
      <AlertTicker />
      <SignalTicker />
    </div>
  );
}
