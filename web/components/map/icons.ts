// Programmatic vessel glyph — a hull/arrow drawn on a canvas and exported as a
// data URI. No external image assets. White so the IconLayer can tint per
// ship-type via getColor.

let cached: string | null = null;

export function vesselArrowDataURI(): string {
  if (cached) return cached;
  const size = 64;
  // SSR guard — return a 1x1 transparent pixel; the real one is built client-side.
  if (typeof document === "undefined") {
    return "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
  }
  const c = document.createElement("canvas");
  c.width = size;
  c.height = size;
  const ctx = c.getContext("2d");
  if (!ctx) return "";
  ctx.clearRect(0, 0, size, size);

  // Arrow pointing "up" (north). cog rotation handled by the layer.
  const cx = size / 2;
  ctx.beginPath();
  ctx.moveTo(cx, 6); // nose
  ctx.lineTo(size - 12, size - 10); // right stern
  ctx.lineTo(cx, size - 22); // notch
  ctx.lineTo(12, size - 10); // left stern
  ctx.closePath();

  ctx.fillStyle = "rgba(255,255,255,1)";
  ctx.fill();
  // dark outline so the colored arrow reads against a LIGHT basemap
  ctx.lineJoin = "round";
  ctx.lineWidth = 3; // ~1.5px visual at the rendered icon size
  ctx.strokeStyle = "rgba(20,35,60,0.9)";
  ctx.stroke();

  cached = c.toDataURL("image/png");
  return cached;
}

// IconLayer atlas mapping for the single arrow icon.
export function vesselIconMapping() {
  return {
    arrow: {
      x: 0,
      y: 0,
      width: 64,
      height: 64,
      anchorX: 32,
      anchorY: 32,
      mask: true, // allow getColor tinting
    },
  };
}
