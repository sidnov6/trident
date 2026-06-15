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
        paint: {
          "raster-opacity": 0.85,
          "raster-brightness-min": 0.0,
          "raster-brightness-max": 0.55,
          "raster-contrast": 0.15,
          "raster-saturation": -0.5,
          "raster-hue-rotate": 200, // push toward deep navy
        },
      },
    ],
  };
}
