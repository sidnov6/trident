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
        // Clean light dashboard surfaces
        void: "#eef2f7",
        panel: "#ffffff",
        panel2: "#f5f8fc",
        hairline: "#d8e0ea",
        ink: "#15233a",
        inkdim: "#5a6b80",
        inkfaint: "#9aabbd",
        // Primary accent — clean navy-blue
        amber: "#1f5fbf",
        amberdim: "#9db9e0",
        // Alerts
        alert: "#dc2626",
        info: "#0e9aa7",
        // Threat ladder
        green: "#16a34a",
        elevated: "#d97706",
        high: "#ea580c",
        critical: "#dc2626",
      },
      fontFamily: {
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      boxShadow: {
        glow: "0 1px 3px rgba(20,40,70,0.10), 0 1px 2px rgba(20,40,70,0.06)",
        glowinfo: "0 0 0 1px rgba(14,154,167,0.25)",
        glowalert: "0 0 0 1px rgba(220,38,38,0.35)",
      },
      keyframes: {
        flicker: {
          "0%,100%": { opacity: "1" },
          "50%": { opacity: "1" },
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
