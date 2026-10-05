import * as maplibregl from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type { MapMouseEvent } from "maplibre-gl";
import { useEffect, useRef } from "react";
import type { Region, RegionCode } from "@/features/data/types";
export default function RegionMap({
  regions,
  selected,
  onSelect,
}: {
  regions: Region[];
  selected: RegionCode;
  onSelect: (code: RegionCode) => void;
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  useEffect(() => {
    if (!el.current || map.current) return;
    maplibregl.setWorkerUrl(workerUrl);
    const instance = new maplibregl.Map({
      container: el.current,
      style: {
        version: 8,
        sources: {
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
              "raster-opacity": 0.42,
              "raster-saturation": -1,
              "raster-brightness-min": 0.12,
              "raster-brightness-max": 0.72,
            },
          },
        ],
      },
      center: [79.5, 20],
      zoom: 3.35,
      attributionControl: true,
    });
    instance.on("load", () => {
      instance.addSource("regions", {
        type: "geojson",
        data: {
          type: "FeatureCollection",
          features: regions.map((r) => ({
            type: "Feature",
            properties: { code: r.code, name: r.name },
            geometry: { type: "Point", coordinates: [r.lon, r.lat] },
          })),
        },
      });
      instance.addLayer({
        id: "region-rings",
        type: "circle",
        source: "regions",
        paint: {
          "circle-radius": ["case", ["==", ["get", "code"], selected], 18, 11],
          "circle-color": ["case", ["==", ["get", "code"], selected], "#45e6d3", "#f5b54c"],
          "circle-opacity": 0.22,
          "circle-stroke-width": 2,
          "circle-stroke-color": ["case", ["==", ["get", "code"], selected], "#45e6d3", "#f5b54c"],
        },
      });
      regions.forEach((region) => {
        const marker = document.createElement("button");
        marker.type = "button";
        marker.className = `region-marker ${region.code === selected ? "selected" : ""}`;
        marker.textContent = region.code;
        marker.title = region.name;
        marker.addEventListener("click", () => onSelect(region.code));
        new maplibregl.Marker({ element: marker })
          .setLngLat([region.lon, region.lat])
          .addTo(instance);
      });
      instance.on(
        "click",
        "region-rings",
        (e: MapMouseEvent & { features?: Array<{ properties?: { code?: unknown } }> }) => {
          const code = e.features?.[0]?.properties?.code;
          if (code === "KWG" || code === "BOB" || code === "IGP") onSelect(code);
        },
      );
      instance.on(
        "mouseenter",
        "region-rings",
        () => (instance.getCanvas().style.cursor = "pointer"),
      );
      instance.on("mouseleave", "region-rings", () => (instance.getCanvas().style.cursor = ""));
    });
    map.current = instance;
    return () => {
      instance.remove();
      map.current = null;
    };
  }, [regions, onSelect, selected]);
  useEffect(() => {
    const instance = map.current;
    if (instance?.getLayer("region-rings"))
      instance.setPaintProperty("region-rings", "circle-radius", [
        "case",
        ["==", ["get", "code"], selected],
        18,
        11,
      ]);
  }, [selected]);
  return <div className="region-map" ref={el} aria-label="Map of Synoptiq pilot regions" />;
}
