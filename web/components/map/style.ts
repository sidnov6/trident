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
        // light_all (CARTO Positron) carries country borders, place + sea labels
        // so the world view reads as a clean, legible light map.
        tiles: [
          "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
          "https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
          "https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
        ],
        tileSize: 256,
        attribution: "© OpenStreetMap © CARTO",
      },
    },
    layers: [
      {
        id: "void",
        type: "background",
        paint: { "background-color": "#eef2f7" },
      },
      {
        id: "basemap",
        type: "raster",
        source: "basemap",
        // Clean, legible light basemap. A slight desaturation keeps it calm
        // under the data layers without crushing contrast.
        paint: {
          "raster-opacity": 1.0,
          "raster-saturation": -0.1,
          "raster-contrast": 0.0,
        },
      },
    ],
  };
}
