import { useCallback, useEffect, useState } from "react";

import CrossbreedModal from "../components/CrossbreedModal/CrossbreedModal";
import ZoneMap from "../components/Map/ZoneMap";
import ZoneBlock from "../components/ZoneBlock/ZoneBlock";
import {
  api,
  fmt,
  formatINR,
  selectionEquity,
  sumCost,
  sumImpact,
  type CityBaseline,
  type EquityCheck,
  type Zone,
} from "../lib/api";
import "./Dashboard.css";

/* Page 2 — the dashboard.
 *
 * Selection order is the state of record. Clicking a zone appends its block to the
 * bottom of the panel and leaves existing blocks alone; clicking the same zone
 * again removes only that block. So selection is an ordered list, not a Set —
 * a Set would lose insertion order and the brief specifies append-to-bottom.
 */
export default function Dashboard({ onBack }: { onBack: () => void }) {
  const [zones, setZones] = useState<Zone[]>([]);
  /* Whether the ranking as a whole leans toward one end of the housing record.
   * Computed server-side over every zone, so it describes the model's ranking rather
   * than whatever the user has currently clicked. */
  const [equityCheck, setEquityCheck] = useState<EquityCheck | null>(null);
  const [city, setCity] = useState<CityBaseline | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /* The zone whose crossbreed popup is open. Holds the full Zone (with species)
   * that ZoneBlock already fetched, so the modal never re-requests it. */
  const [crossbreedZone, setCrossbreedZone] = useState<Zone | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([api.zones(), api.city()])
      .then(([z, c]) => {
        if (!alive) return;
        setZones(z.zones);
        setEquityCheck(z.equity_check);
        setCity(c);
      })
      .catch((e) => alive && setError(String(e.message ?? e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const toggle = useCallback((zoneId: string) => {
    setSelected((prev) =>
      prev.includes(zoneId) ? prev.filter((id) => id !== zoneId) : [...prev, zoneId],
    );
  }, []);

  const byId = new Map(zones.map((z) => [z.zone_id, z]));
  const selectedZones = selected
    .map((id) => byId.get(id))
    .filter((z): z is Zone => z !== undefined);

  /* Summed client-side from data already in hand, so the top bar updates on the
   * same frame as the click — no request per selection. */
  const impact = sumImpact(selectedZones);
  const cost = sumCost(selectedZones);

  return (
    <div className="dash">
      {/* --- top bar ---------------------------------------------------------
        * No branding here by design — the back arrow is the only chrome, and which
        * app this is is already established by the screen you came from. */}
      <header className="dash-top">
        <button type="button" className="dash-back" onClick={onBack} aria-label="Back to city selection">
          <svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.7">
            <path d="M13 8H4M7 4L3 8l4 4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>

        <div className="dash-metrics">
          <Metric
            label="City AQI"
            value={fmt(city?.aqi, 0)}
            sub={city?.aqi_band ?? "—"}
          />
          <Metric
            label="PM2.5"
            value={fmt(city?.pm25_ugm3, 0)}
            sub="µg/m³"
          />
          <Metric
            label="Hottest zone"
            value={fmt(
              zones.length
                ? Math.max(
                    ...zones
                      .map((z) => z.measurements.land_surface_temp_c)
                      .filter((v): v is number => v !== null),
                  )
                : null,
              1,
            )}
            sub="°C surface"
          />

          <div className="dash-divider" aria-hidden="true" />

          {/* Projection block — everything right of the divider is a forecast of
            * the current selection, not a measurement of Delhi today. */}
          <Metric
            label="Zones selected"
            value={String(selected.length)}
            sub={`of ${zones.length}`}
            emphasis={selected.length > 0}
          />
          <Metric
            label="Plants projected"
            value={impact.plant_count.toLocaleString()}
            sub={
              selected.length === 0
                ? "select zones"
                : impact.zones_with_capacity < selected.length
                  ? `${selected.length - impact.zones_with_capacity} already green`
                  : "at capacity"
            }
            emphasis={impact.plant_count > 0}
          />
          <Metric
            label="CO₂ captured"
            value={impact.co2_tonnes_yr > 0 ? impact.co2_tonnes_yr.toFixed(2) : "0"}
            sub="tonnes/yr at maturity"
            emphasis={impact.co2_tonnes_yr > 0}
          />
          <Metric
            label="PM captured"
            value={
              impact.pm_deposition_kg_yr > 0
                ? impact.pm_deposition_kg_yr.toFixed(0)
                : "0"
            }
            sub="kg/yr at maturity"
            emphasis={impact.pm_deposition_kg_yr > 0}
          />
          <Metric
            label="Est. cost"
            value={formatINR(cost.total_inr)}
            sub={
              selected.length === 0
                ? "published unit rate"
                : cost.mismatched_placements > 0
                  ? `${cost.mismatched_placements} non-ground placement`
                  : "planting + 10 yr upkeep"
            }
            emphasis={cost.total_inr > 0}
          />
        </div>
      </header>

      {/* --- body ------------------------------------------------------------ */}
      <div className="dash-body">
        <section className="dash-map" aria-label="Priority zone map">
          {loading && <div className="dash-loading">Loading zones…</div>}
          {error && (
            <div className="dash-error card">
              <strong>Couldn't load zone data</strong>
              <p className="muted">{error}</p>
              <p className="provenance">
                Start the API with{" "}
                <code>.venv/bin/uvicorn backend.app.main:app --reload</code>
              </p>
            </div>
          )}
          {!loading && !error && (
            <ZoneMap zones={zones} selectedIds={selected} onToggle={toggle} />
          )}
        </section>

        <aside className="dash-panel scroll-y" aria-label="Selected zones">
          {selectedZones.length === 0 ? (
            <div className="dash-empty">
              <div className="dash-empty-glyph" aria-hidden="true">
                🌱
              </div>
              <h2>Select a zone to begin.</h2>
            </div>
          ) : (
            <div className="dash-blocks">
              <EquityPanel zones={selectedZones} ranking={equityCheck} />
              {selectedZones.map((zone) => (
                <ZoneBlock
                  key={zone.zone_id}
                  zone={zone}
                  onRemove={() => toggle(zone.zone_id)}
                  onExploreCrossbreeds={setCrossbreedZone}
                />
              ))}
            </div>
          )}
        </aside>
      </div>

      {crossbreedZone && (
        <CrossbreedModal
          zone={crossbreedZone}
          onClose={() => setCrossbreedZone(null)}
        />
      )}
    </div>
  );
}

/* Equity transparency check.
 *
 * Two separate statements, deliberately not merged: one about the zones the user has
 * chosen, one about the ranking the model produced. Neither changes any score — this
 * panel reports, and the priority score is computed without this signal entirely.
 *
 * The proxy qualifier is rendered from the payload rather than written in here, so
 * the disclaimer cannot drift from the data it describes.
 */
function EquityPanel({
  zones,
  ranking,
}: {
  zones: Zone[];
  ranking: EquityCheck | null;
}) {
  if (!zones.length) return null;

  const selection = selectionEquity(zones); // null for a single zone
  const first = zones[0].equity;
  const proxyLabel = first?.proxy_label ?? ranking?.proxy_label;
  const km = first ? (first.range_m / 1000).toFixed(1) : null;

  // Caveats are dataset-level and identical across zones, so one copy is shown
  // rather than the same four sentences repeated per selected zone.
  const caveats = first?.caveats ?? [];
  const notes =
    caveats.length + 2 + (ranking?.applicable ? 1 : 0) + (ranking?.note ? 1 : 0);

  return (
    <section className="equity-panel card" aria-label="Equity check">
      <span className="eyebrow">Equity check</span>

      {selection && <p className="equity-line">{selection.summary}</p>}

      {/* Per-zone counts live here rather than in each zone block. They were the
        * only thing the per-zone equity section carried that this box did not, so
        * they moved in with it rather than being dropped. */}
      <ul className="equity-zones">
        {zones.map((z) => (
          <li key={z.zone_id}>
            <span className="equity-zone-name">{z.name}</span>
            <span className="equity-zone-count tabular">
              {z.equity.clusters_in_zone} in zone ·{" "}
              {z.equity.clusters_within_range} within {km} km
            </span>
          </li>
        ))}
      </ul>

      {/* Never behind a click. */}
      {proxyLabel && (
        <p className="provenance equity-proxy">{proxyLabel}.</p>
      )}

      <details className="equity-details">
        <summary className="provenance">How this check works ({notes})</summary>
        <ul>
          <li className="provenance">
            A J.J. basti is a jhuggi-jhopri cluster — informal housing recorded by
            the Delhi Urban Shelter Improvement Board.
          </li>
          {ranking?.applicable && (
            <li className="provenance">{ranking.summary}</li>
          )}
          {ranking?.note && <li className="provenance">{ranking.note}</li>}
          {caveats.map((c) => (
            <li key={c} className="provenance">
              {c}
            </li>
          ))}
          <li className="provenance">Source: {first?.source}</li>
        </ul>
      </details>
    </section>
  );
}

function Metric({
  label,
  value,
  sub,
  emphasis,
}: {
  label: string;
  value: string;
  sub: string;
  emphasis?: boolean;
}) {
  return (
    <div className={`metric${emphasis ? " metric-on" : ""}`}>
      <span className="eyebrow">{label}</span>
      <span className="metric-value tabular">{value}</span>
      <span className="metric-sub faint">{sub}</span>
    </div>
  );
}
