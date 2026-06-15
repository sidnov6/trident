import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Near-black ops-room surfaces
        void: "#05070a",
        panel: "#0a0e14",
        panel2: "#0d121a",
        hairline: "#1a232e",
        ink: "#c7d2dd",
        inkdim: "#6b7c8c",
        inkfaint: "#3a4856",
        // One warm accent
        amber: "#ffb000",
        amberdim: "#7a5500",
        // Two alerts
        alert: "#ff2e3e",
        info: "#2ee6ff",
        // Threat ladder
        green: "#1fd65f",
        elevated: "#ffd400",
        high: "#ff8a00",
        critical: "#ff2e3e",
      },
      fontFamily: {
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      boxShadow: {
        glow: "0 0 12px rgba(255,176,0,0.35)",
        glowinfo: "0 0 12px rgba(46,230,255,0.35)",
        glowalert: "0 0 16px rgba(255,46,62,0.5)",
      },
      keyframes: {
        flicker: {
          "0%,100%": { opacity: "1" },
          "50%": { opacity: "0.82" },
        },
        scan: {
          "0%": { transform: "translateY(-100%)" },
          "100%": { transform: "translateY(100%)" },
        },
      },
      animation: {
        flicker: "flicker 3s ease-in-out infinite",
        scan: "scan 7s linear infinite",
      },
    },
  },
  plugins: [],
};

export default config;
