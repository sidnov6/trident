"use client";

import dynamic from "next/dynamic";
import StatusBar from "@/components/statusbar/StatusBar";
import LeftRail from "@/components/rails/LeftRail";
import IncidentFeed from "@/components/rails/IncidentFeed";
import VesselDossierPanel from "@/components/rails/VesselDossier";
import SignalTicker from "@/components/ticker/SignalTicker";
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

        {/* center: map (most pixels) */}
        <main className="relative min-w-0 flex-1">
          <MapView />
          <VesselDossierPanel />
        </main>

        <IncidentFeed />
      </div>

      {/* bottom strip */}
      <div className="flex h-12 shrink-0 items-stretch border-t border-hairline bg-panel">
        <CongestionStrip />
        <ReplayScrubber />
      </div>
      <SignalTicker />
    </div>
  );
}
