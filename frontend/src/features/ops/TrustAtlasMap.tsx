import * as maplibregl from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { useEffect, useRef } from "react";

type Zone = { zone: string; lat: number; lon: number; model: string; colour: string };

// Internal zone ids -> the public AOI codes used everywhere else in the UI
// (region selectors, forecast, replay). The map labels by AOI, not by model:
// the model is shown only as a secondary line under the AOI code.
const AOI_CODES: Record<string, string> = {
  kerala_western_ghats: "KWG",
  bay_of_bengal_east_coast: "BOB",
  indo_gangetic_plains: "IGP",
};

function aoiCode(zone: string): string {
  return AOI_CODES[zone] ?? zone;
}

function zonesToFeatureCollection(zones: Zone[]) {
  return {
    type: "FeatureCollection" as const,
    features: zones.map((zone) => ({
      type: "Feature" as const,
      properties: {
        zone: zone.zone,
        aoi: aoiCode(zone.zone),
        model: zone.model,
        colour: zone.colour,
      },
      geometry: { type: "Point" as const, coordinates: [zone.lon, zone.lat] },
    })),
  };
}

function syncMarkers(
  instance: maplibregl.Map,
  zones: Zone[],
  markers: maplibregl.Marker[],
  selected: string,
  onSelect: (zone: string) => void,
) {
  markers.forEach((marker) => marker.remove());
  markers.splice(
    0,
    markers.length,
    ...zones.map((zone) => {
      const label = document.createElement("button");
      label.type = "button";
      label.className = `atlas-zone-label${zone.zone === selected ? " selected" : ""}`;
      label.setAttribute("aria-label", `Select ${aoiCode(zone.zone)}, ${zone.model} dominates`);
      label.style.setProperty("--zone-colour", zone.colour);

      const code = document.createElement("b");
      code.textContent = aoiCode(zone.zone);
      const model = document.createElement("small");
      model.textContent = zone.model;

      label.append(code, model);
      label.addEventListener("click", (event) => {
        event.stopPropagation();
        onSelect(zone.zone);
      });
      return new maplibregl.Marker({ element: label, anchor: "top", offset: [0, 14] })
        .setLngLat([zone.lon, zone.lat])
        .addTo(instance);
    }),
  );
}

export function TrustAtlasMap({
  zones,
  selected,
  onSelect,
}: {
  zones: Zone[];
  selected: string;
  onSelect: (zone: string) => void;
}) {
  const element = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  // Latest zones, read by the map's `load` handler so the init effect does not
  // need `zones` as a dependency (which used to rebuild the whole map).
  const zonesRef = useRef(zones);
  zonesRef.current = zones;

  // Initialise MapLibre exactly once for the lifetime of the component.
  useEffect(() => {
    if (!element.current || map.current) return;
    maplibregl.setWorkerUrl(workerUrl);
    const instance = new maplibregl.Map({
      container: element.current,
      center: [79.2, 22.4],
      zoom: 4.5,
      minZoom: 3.8,
      maxZoom: 10,
      maxBounds: [
        [64, 3],
        [101, 39],
      ],
      style: {
        version: 8,
        sources: {
          india: {
            type: "geojson",
            data: "https://raw.githubusercontent.com/datameet/maps/master/Country/india-land-simplified.geojson",
          },
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution: "© OpenStreetMap contributors",
          },
        },
        layers: [
          { id: "background", type: "background", paint: { "background-color": "#07131b" } },
          {
            id: "osm-basemap",
            type: "raster",
            source: "osm",
            paint: {
              "raster-opacity": 0.35,
              "raster-saturation": -0.75,
              "raster-brightness-min": 0.12,
              "raster-brightness-max": 0.78,
            },
          },
          {
            id: "india-fill",
            type: "fill",
            source: "india",
            paint: { "fill-color": "#1bc8aa", "fill-opacity": 0.045 },
          },
          {
            id: "india-boundary",
            type: "line",
            source: "india",
            paint: { "line-color": "#45e6d3", "line-width": 1.5, "line-opacity": 0.7 },
          },
        ],
      },
      attributionControl: {},
    });
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    instance.on("load", () => {
      instance.fitBounds(
        [
          [67, 5],
          [98, 38],
        ],
        { padding: 18, duration: 0 },
      );
      // Seed the zone source from whatever the latest props are.
      instance.addSource("atlas-zones", {
        type: "geojson",
        data: zonesToFeatureCollection(zonesRef.current),
      });
      instance.addLayer({
        id: "atlas-zone-halo",
        type: "circle",
        source: "atlas-zones",
        paint: {
          "circle-radius": ["case", ["==", ["get", "zone"], selectedRef.current], 34, 26],
          "circle-color": ["get", "colour"],
          "circle-opacity": ["case", ["==", ["get", "zone"], selectedRef.current], 0.38, 0.24],
          "circle-blur": 0.85,
          "circle-stroke-width": 2,
          "circle-stroke-color": ["get", "colour"],
        },
      });
      instance.addLayer({
        id: "atlas-zone-dot",
        type: "circle",
        source: "atlas-zones",
        paint: {
          "circle-radius": ["case", ["==", ["get", "zone"], selectedRef.current], 9, 7],
          "circle-color": ["get", "colour"],
          "circle-opacity": 1,
          "circle-stroke-width": 2,
          "circle-stroke-color": "#e6f1f8",
        },
      });
      instance.on("click", "atlas-zone-halo", (event) => {
        const zone = event.features?.[0]?.properties?.["zone"];
        if (typeof zone === "string") onSelectRef.current(zone);
      });
      instance.on("mouseenter", "atlas-zone-halo", () => {
        instance.getCanvas().style.cursor = "pointer";
      });
      instance.on("mouseleave", "atlas-zone-halo", () => {
        instance.getCanvas().style.cursor = "";
      });
      syncMarkers(
        instance,
        zonesRef.current,
        markers.current,
        selectedRef.current,
        onSelectRef.current,
      );
    });
    map.current = instance;
    return () => {
      markers.current.forEach((marker) => marker.remove());
      markers.current = [];
      instance.remove();
      map.current = null;
    };
  }, []);

  // Push new zone data into the EXISTING source. The map is never recreated.
  useEffect(() => {
    const instance = map.current;
    if (!instance || !instance.isStyleLoaded()) return;
    const source = instance.getSource("atlas-zones") as maplibregl.GeoJSONSource | undefined;
    source?.setData(zonesToFeatureCollection(zones));
    syncMarkers(instance, zones, markers.current, selectedRef.current, onSelectRef.current);
  }, [zones]);

  useEffect(() => {
    const instance = map.current;
    if (!instance?.isStyleLoaded() || !instance.getLayer("atlas-zone-halo")) return;
    instance.setPaintProperty("atlas-zone-halo", "circle-radius", [
      "case",
      ["==", ["get", "zone"], selected],
      34,
      26,
    ]);
    instance.setPaintProperty("atlas-zone-halo", "circle-opacity", [
      "case",
      ["==", ["get", "zone"], selected],
      0.38,
      0.24,
    ]);
    instance.setPaintProperty("atlas-zone-dot", "circle-radius", [
      "case",
      ["==", ["get", "zone"], selected],
      9,
      7,
    ]);
    syncMarkers(instance, zonesRef.current, markers.current, selected, onSelectRef.current);
  }, [selected]);

  return (
    <div
      className="atlas-map-real"
      ref={element}
      aria-label="Accurate India map showing Synoptiq pilot zones"
    />
  );
}
