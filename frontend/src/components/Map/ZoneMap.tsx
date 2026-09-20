import { useMemo } from "react";
import L from "leaflet";
import { MapContainer, Marker, TileLayer, Tooltip } from "react-leaflet";

import {
  PLACEMENT_LABEL,
  PRIORITY_VAR,
  fmt,
  type Zone,
} from "../../lib/api";
import "./ZoneMap.css";

/* Interactive map of the 10 priority zones.
 *
 * Two visual channels, kept independent so neither hides the other:
 *   - fill colour  = priority band (Critical..Low)
 *   - glyph        = placement category (ground / wall / potted)
 *
 * The glyph is why these are divIcon markers rather than CircleMarker: a circle
 * can carry colour but not a shape cue, and the legend promises both. Colour
 * alone would also leave the priority ramp unreadable to anyone with a red-green
 * deficiency, so the glyph is a genuine second channel, not decoration.
 *
 * Size is NOT bound to priority. Encoding the same variable twice would make
 * low-priority zones physically hard to click, and area is read inconsistently
 * anyway. Selection is shown with a ring, which is unambiguous at any size.
 *
 * Basemap is OpenStreetMap standard tiles — keyless. CARTO's Positron looks
 * better but now demands an API key and stamps "API KEY REQUIRED" across every
 * tile without one. The desaturating filter in base.css mutes OSM's colour to
 * the sage the brief asks for, so markers stay the only saturated thing.
 */

const DELHI_CENTER: [number, number] = [28.6139, 77.209];

/* Simple inline glyphs per placement — a shape cue that survives greyscale
 * printing and colour-vision deficiency, unlike colour alone. */
const PLACEMENT_GLYPH: Record<string, string> = {
  ground: "▲",
  wall_hanging: "▮",
  potted: "●",
};

export default function ZoneMap({
  zones,
  selectedIds,
  onToggle,
}: {
  zones: Zone[];
  selectedIds: string[];
  onToggle: (zoneId: string) => void;
}) {
  /* Rebuild icons only when selection changes — Leaflet re-renders the DOM node
   * on every icon identity change, which would otherwise thrash on each paint. */
  const icons = useMemo(() => {
    const map = new Map<string, L.DivIcon>();
    for (const zone of zones) {
      const selected = selectedIds.includes(zone.zone_id);
      const size = selected ? 34 : 28;
      map.set(
        zone.zone_id,
        L.divIcon({
          className: "zone-pin-wrap",
          html: `<span class="zone-pin${selected ? " is-selected" : ""}"
                       style="--pin:${PRIORITY_VAR[zone.priority_band]};--size:${size}px"
                       aria-hidden="true">${PLACEMENT_GLYPH[zone.placement] ?? "●"}</span>`,
          iconSize: [size, size],
          iconAnchor: [size / 2, size / 2],
        }),
      );
    }
    return map;
  }, [zones, selectedIds]);

  return (
    <div className="zone-map">
      <MapContainer
        center={DELHI_CENTER}
        zoom={11}
        scrollWheelZoom
        className="zone-map-canvas"
      >
        <TileLayer
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          maxZoom={19}
        />

        {zones.map((zone) => {
          const selected = selectedIds.includes(zone.zone_id);
          const colour = PRIORITY_VAR[zone.priority_band];
          return (
            <Marker
              key={zone.zone_id}
              position={[zone.latitude, zone.longitude]}
              icon={icons.get(zone.zone_id)!}
              eventHandlers={{ click: () => onToggle(zone.zone_id) }}
              title={zone.name}
            >
              <Tooltip direction="top" offset={[0, -18]} opacity={1}>
                <div className="zone-tip">
                  <strong>{zone.name}</strong>
                  <div className="zone-tip-row">
                    <span
                      className="zone-tip-swatch"
                      style={{ background: colour }}
                      aria-hidden="true"
                    />
                    {zone.priority_band} priority · {zone.priority_score.toFixed(2)}
                  </div>
                  <div className="zone-tip-row faint">
                    {PLACEMENT_GLYPH[zone.placement]}{" "}
                    {PLACEMENT_LABEL[zone.placement]} · AQI{" "}
                    {fmt(zone.measurements.aqi, 0)} · {zone.vegetation.label}
                  </div>
                  <div className="zone-tip-action">
                    {selected ? "Click to remove" : "Click to add"}
                  </div>
                </div>
              </Tooltip>
            </Marker>
          );
        })}
      </MapContainer>

      <div className="map-legend card">
        <div className="legend-group">
          <span className="eyebrow">Priority</span>
          <ul>
            {(["Critical", "High", "Moderate", "Low"] as const).map((band) => (
              <li key={band}>
                <span
                  className="legend-swatch"
                  style={{ background: PRIORITY_VAR[band] }}
                  aria-hidden="true"
                />
                {band}
              </li>
            ))}
          </ul>
        </div>

        <div className="legend-group">
          <span className="eyebrow">Placement</span>
          <ul>
            {(["ground", "wall_hanging", "potted"] as const).map((p) => (
              <li key={p}>
                <span className="legend-glyph" aria-hidden="true">
                  {PLACEMENT_GLYPH[p]}
                </span>
                {PLACEMENT_LABEL[p]}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
