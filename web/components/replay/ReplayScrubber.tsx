"use client";

import { useState } from "react";
import { useStore } from "@/lib/store";
import { replayStreamURL } from "@/lib/api";

// Forensic mode: scrub a historical window for the focused vessel. Opens a
// replay WS and reports frame progress. Kept self-contained — if the replay
// service is down it surfaces an offline state rather than crashing.
export default function ReplayScrubber() {
  const replayMode = useStore((s) => s.replayMode);
  const setReplayMode = useStore((s) => s.setReplayMode);
  const dossierMmsi = useStore((s) => s.dossierMmsi);

  const [speed, setSpeed] = useState(8);
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState<"idle" | "playing" | "offline" | "done">(
    "idle"
  );
  const [ws, setWs] = useState<WebSocket | null>(null);

  const startReplay = () => {
    if (ws) {
      ws.close();
      setWs(null);
    }
    const now = Math.floor(Date.now() / 1000);
    const url = replayStreamURL({
      mmsi: dossierMmsi ?? undefined,
      from: now - 6 * 3600,
      to: now,
      speed,
    });
    let sock: WebSocket;
    try {
      sock = new WebSocket(url);
    } catch {
      setStatus("offline");
      return;
    }
    setStatus("playing");
    setProgress(0);
    sock.onmessage = (ev) => {
      try {
        const m = JSON.parse(ev.data as string) as { progress?: number };
        if (typeof m.progress === "number") setProgress(m.progress);
      } catch {
        /* ignore non-progress frames */
      }
    };
    sock.onerror = () => setStatus("offline");
    sock.onclose = () =>
      setStatus((s) => (s === "playing" ? "done" : s === "offline" ? "offline" : "idle"));
    setWs(sock);
  };

  const stopReplay = () => {
    ws?.close();
    setWs(null);
    setStatus("idle");
  };

  return (
    <div className="flex h-full flex-1 items-center gap-3 px-3">
      <button
        onClick={() => setReplayMode(!replayMode)}
        className={`border px-2 py-0.5 text-[10px] uppercase tracking-wider ${
          replayMode
            ? "border-amber text-amber shadow-glow"
            : "border-hairline text-inkdim hover:text-ink"
        }`}
      >
        Forensic {replayMode ? "ON" : "OFF"}
      </button>

      {replayMode && (
        <>
          <span className="text-[10px] text-inkfaint">
            {dossierMmsi ? `MMSI ${dossierMmsi}` : "all vessels"}
          </span>

          <button
            onClick={status === "playing" ? stopReplay : startReplay}
            className="border border-info/50 px-2 py-0.5 text-[10px] uppercase text-info hover:bg-info/10"
          >
            {status === "playing" ? "■ stop" : "▶ replay 6h"}
          </button>

          <label className="flex items-center gap-1 text-[10px] text-inkfaint">
            speed
            <input
              type="range"
              min={1}
              max={32}
              value={speed}
              onChange={(e) => setSpeed(Number(e.target.value))}
              className="accent-amber"
            />
            <span className="tabular-nums text-inkdim">{speed}×</span>
          </label>

          <div className="relative h-1 flex-1 bg-hairline">
            <div
              className="absolute inset-y-0 left-0 bg-amber"
              style={{ width: `${Math.round(progress * 100)}%` }}
            />
          </div>

          <span
            className={`text-[10px] uppercase ${
              status === "offline" ? "text-alert" : "text-inkfaint"
            }`}
          >
            {status === "offline" ? "replay offline" : status}
          </span>
        </>
      )}
    </div>
  );
}
