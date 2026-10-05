import * as maplibregl from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { useEffect, useRef } from "react";

type Zone = { zone: string; lat: number; lon: number; model: string; colour: string };

const AOI_CODES: Record<string, string> = {
  kerala_western_ghats: "KWG",
  bay_of_bengal_east_coast: "BOB",
  indo_gangetic_plains: "IGP",
};

function aoiCode(zone: string): string {
  return AOI_CODES[zone] ?? zone;
}

function positionMarkers(instance: maplibregl.Map, zones: Zone[], markers: HTMLButtonElement[]) {
  zones.forEach((zone, index) => {
    const marker = markers[index];
    if (!marker) return;
    const point = instance.project([zone.lon, zone.lat]);
    marker.style.left = `${point.x}px`;
    marker.style.top = `${point.y}px`;
    marker.hidden =
      point.x < 0 ||
      point.y < 0 ||
      point.x > instance.getContainer().clientWidth ||
      point.y > instance.getContainer().clientHeight;
  });
}

function syncMarkers(
  instance: maplibregl.Map,
  zones: Zone[],
  overlay: HTMLDivElement,
  markers: HTMLButtonElement[],
  selected: string,
  onSelect: (zone: string) => void,
) {
  markers.forEach((marker) => marker.remove());
  markers.splice(
    0,
    markers.length,
    ...zones.map((zone) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `atlas-zone-label${zone.zone === selected ? " selected" : ""}`;
      button.setAttribute("aria-label", `Select ${aoiCode(zone.zone)}, ${zone.model} dominates`);
      button.setAttribute("aria-pressed", String(zone.zone === selected));
      button.style.setProperty("--zone-colour", zone.colour);

      const dot = document.createElement("span");
      dot.className = "atlas-zone-dot";
      dot.setAttribute("aria-hidden", "true");
      const label = document.createElement("span");
      label.className = "atlas-zone-caption";
      const code = document.createElement("b");
      code.textContent = aoiCode(zone.zone);
      const model = document.createElement("small");
      model.textContent = zone.model;
      label.append(code, model);
      button.append(dot, label);

      button.addEventListener("click", (event) => {
        event.stopPropagation();
        onSelect(zone.zone);
      });
      overlay.append(button);
      return button;
    }),
  );
  positionMarkers(instance, zones, markers);
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
  const overlay = useRef<HTMLDivElement | null>(null);
  const markers = useRef<HTMLButtonElement[]>([]);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const zonesRef = useRef(zones);
  zonesRef.current = zones;

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
    const updateMarkerPositions = () =>
      positionMarkers(instance, zonesRef.current, markers.current);
    instance.on("load", () => {
      instance.fitBounds(
        [
          [64, 0],
          [101, 40],
        ],
        { padding: 18, duration: 0 },
      );
      const markerOverlay = document.createElement("div");
      markerOverlay.className = "atlas-zone-overlay";
      element.current?.append(markerOverlay);
      overlay.current = markerOverlay;
      syncMarkers(
        instance,
        zonesRef.current,
        markerOverlay,
        markers.current,
        selectedRef.current,
        onSelectRef.current,
      );
    });
    for (const event of ["move", "resize", "zoom", "rotate", "pitch"] as const) {
      instance.on(event, updateMarkerPositions);
    }
    map.current = instance;
    return () => {
      markers.current.forEach((marker) => marker.remove());
      markers.current = [];
      overlay.current?.remove();
      overlay.current = null;
      instance.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const instance = map.current;
    const markerOverlay = overlay.current;
    if (!instance || !markerOverlay) return;
    syncMarkers(
      instance,
      zones,
      markerOverlay,
      markers.current,
      selectedRef.current,
      onSelectRef.current,
    );
  }, [zones]);

  useEffect(() => {
    const instance = map.current;
    const markerOverlay = overlay.current;
    if (!instance || !markerOverlay) return;
    syncMarkers(
      instance,
      zonesRef.current,
      markerOverlay,
      markers.current,
      selected,
      onSelectRef.current,
    );
  }, [selected]);

  return (
    <div
      className="atlas-map-real"
      ref={element}
      aria-label="Accurate India map showing Synoptiq pilot zones"
    />
  );
}
