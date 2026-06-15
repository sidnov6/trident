import type { StyleSpecification } from "maplibre-gl";

// Self-contained near-black raster style. We use CARTO dark-matter (nolabels)
// raster tiles re-tinted toward deep navy via a brightness/contrast push so no
// bright colours compete with the data layers. No API key required.
//
// Falls back gracefully: if tiles fail to load, the void background shows and
// data layers still render over black.
export function darkStyle(): StyleSpecification {
  return {
    version: 8,
    glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
    sources: {
      basemap: {
        type: "raster",
        tiles: [
          "https://a.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
          "https://b.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
          "https://c.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
        ],
        tileSize: 256,
        attribution: "© OpenStreetMap © CARTO",
      },
    },
    layers: [
      {
        id: "void",
        type: "background",
        paint: { "background-color": "#05070a" },
      },
      {
        id: "basemap",
        type: "raster",
        source: "basemap",
        // Keep it dark but LEGIBLE — coastlines, land and the canal must read
        // clearly. The previous heavy dimming (brightness-max 0.55 + hue-rotate)
        // crushed the map into a near-black void.
        paint: {
          "raster-opacity": 1.0,
          "raster-brightness-min": 0.05,
          "raster-brightness-max": 1.0,
          "raster-contrast": 0.1,
          "raster-saturation": -0.2,
        },
      },
    ],
  };
}
