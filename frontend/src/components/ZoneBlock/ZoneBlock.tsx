import { useEffect, useState } from "react";

import {
  PLACEMENT_LABEL,
  PRIORITY_VAR,
  WATER_VAR,
  api,
  fmt,
  formatINR,
  mediaUrl,
  pct,
  type Zone,
} from "../../lib/api";
import "./ZoneBlock.css";

/* One zone's block in the right panel, ordered per the brief:
 *   1. zone name
 *   2. real Street View "before" photo   (wired in build step 8)
 *   3. existing-vegetation signal (NDVI)
 *   4. top 5 suggested species with key qualities
 *   5. "Explore crossbreeds" button      (popup lands in build step 6)
 *
 * Species come from /api/zones/{id}, which runs Model B's shortlist. They are
 * fetched per block on mount rather than up front, so opening one zone does not
 * pay for all ten.
 */
export default function ZoneBlock({
  zone: summary,
  onRemove,
  onExploreCrossbreeds,
}: {
  zone: Zone;
  onRemove: () => void;
  onExploreCrossbreeds: (zone: Zone) => void;
}) {
  const [zone, setZone] = useState<Zone>(summary);
  const [loading, setLoading] = useState(!summary.species);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (summary.species) return;
    let alive = true;
    setLoading(true);
    api
      .zone(summary.zone_id)
      .then((full) => alive && setZone(full))
      .catch((e) => alive && setError(String(e.message ?? e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [summary.zone_id, summary.species]);

  const m = zone.measurements;
  const vegetated = zone.vegetation.has_existing_vegetation;

  /* Disclosure labels quote how much is hidden, so the counts are derived from what
   * actually renders rather than hardcoded — a stale number here is a small lie
   * about how much was left out. */
  const measurementNotes =
    zone.water.caveats.length +
    4 + // groundwater line + resolution note + source + placement rationale
    (zone.water.implication ? 1 : 0) +
    (m.aqi_attribution ? 1 : 0);

  return (
    <article className="zone-block card">
      {/* 1 — name */}
      <header className="zb-head">
        <div className="zb-titles">
          <h3 className="zb-name">{zone.name}</h3>
          <p className="zb-locality muted">{zone.locality}</p>
        </div>
        <button
          type="button"
          className="zb-remove"
          onClick={onRemove}
          aria-label={`Remove ${zone.name}`}
          title="Remove from selection"
        >
          <svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6">
            <path d="M4 4l8 8M12 4l-8 8" strokeLinecap="round" />
          </svg>
        </button>
      </header>

      <div className="zb-badges">
        <span
          className="pill"
          style={{
            background: PRIORITY_VAR[zone.priority_band],
            color: "#fff",
          }}
        >
          {zone.priority_band} · {zone.priority_score.toFixed(2)}
        </span>
        <span className="pill pill-placement">
          {PLACEMENT_LABEL[zone.placement]}
        </span>
        {zone.confidence < 1 && (
          <span className="pill pill-caution">
            {Math.round(zone.confidence * 100)}% signal coverage
          </span>
        )}
      </div>

      {/* 2 — Street View "before" photo.
        * A real photograph or nothing: never a stock or generated stand-in, since
        * the whole point of this panel is showing the site as it actually is. */}
      {zone.street_view?.available && zone.street_view.path ? (
        <figure className="zb-photo-figure">
          <div className="zb-photo">
            <img
              src={mediaUrl(zone.street_view.path)}
              alt={`Street View photograph looking toward ${zone.name}`}
              loading="lazy"
            />
          </div>
          <figcaption className="provenance zb-photo-caption">
            <span>
              {zone.street_view.attribution}
              {zone.street_view.captured && ` · captured ${zone.street_view.captured}`}
            </span>
            {zone.street_view.pano_distance_m !== null && (
              <span className="faint">
                camera {zone.street_view.pano_distance_m.toFixed(0)} m from zone centre
                {zone.street_view.heading !== null &&
                  `, facing ${zone.street_view.heading.toFixed(0)}°`}
              </span>
            )}
          </figcaption>
        </figure>
      ) : (
        <div
          className="zb-photo"
          role="img"
          aria-label={`No Street View photograph available for ${zone.name}`}
        >
          <div className="zb-photo-pending">
            <span className="eyebrow">Street View “before”</span>
            <p className="provenance">
              {zone.street_view?.detail ??
                "No photograph available. Left empty rather than substituting a stand-in image."}
            </p>
          </div>
        </div>
      )}

      {/* 3 — vegetation signal */}
      <section className="zb-section">
        <span className="eyebrow">Existing vegetation</span>
        <div className="zb-veg">
          <span
            className={`pill ${vegetated === null ? "pill-caution" : vegetated ? "pill-veg" : "pill-bare"}`}
          >
            {zone.vegetation.label}
          </span>
          {m.ndvi !== null && (
            <span className="zb-veg-detail muted tabular">
              NDVI {m.ndvi.toFixed(3)} (vegetated above{" "}
              {zone.vegetation.ndvi_threshold.toFixed(2)})
            </span>
          )}
        </div>
      </section>

      {/* measurements */}
      <section className="zb-section">
        <span className="eyebrow">Measured conditions</span>
        <dl className="zb-metrics">
          <div>
            <dt>AQI</dt>
            <dd className="tabular">{fmt(m.aqi, 0)}</dd>
          </div>
          <div>
            <dt>PM2.5</dt>
            <dd className="tabular">{fmt(m.pm25_ugm3, 0, " µg/m³")}</dd>
          </div>
          <div>
            <dt>Surface temp</dt>
            <dd className="tabular">{fmt(m.land_surface_temp_c, 1, " °C")}</dd>
          </div>
          <div>
            <dt>vs city median</dt>
            <dd className="tabular">
              {m.heat_island_delta_c === null
                ? "—"
                : `${m.heat_island_delta_c > 0 ? "+" : ""}${m.heat_island_delta_c.toFixed(1)} °C`}
            </dd>
          </div>
          <div>
            <dt>Plantable ground</dt>
            <dd className="tabular">{pct(m.plantable_ground_ratio)}</dd>
          </div>
          <div>
            <dt>Floor-area ratio</dt>
            <dd className="tabular">{fmt(m.floor_area_ratio, 2)}</dd>
          </div>
          {/* Block-level, and labelled as such right in the term rather than only in
            * the note beneath — the qualifier has to survive someone reading the
            * figure alone. */}
          <div>
            <dt>
              Groundwater
              <span className="zb-scope">block</span>
            </dt>
            <dd
              className="tabular"
              style={
                zone.water.category
                  ? { color: WATER_VAR[zone.water.category] }
                  : undefined
              }
            >
              {zone.water.category_label ?? "No CGWB data"}
            </dd>
          </div>
        </dl>

        {/* The figures above stay visible; every sentence explaining where they came
          * from and what they do not cover collapses in here. Groundwater, air
          * quality and the placement call share one disclosure because they are all
          * answering the same question — how much to trust the row above. */}
        <details className="zb-gaps">
          <summary className="provenance">
            How these measurements work ({measurementNotes})
          </summary>
          <ul>
            <li className="provenance">
              Groundwater:{" "}
              {zone.water.available
                ? `${zone.water.category_label} (CGWB, ${zone.water.assessment_year})`
                : "not published for this block"}
              {zone.water.tehsil && ` · ${zone.water.tehsil} block`}
            </li>
            {/* The backend already emits a caveat naming the missing tehsil and the
              * no-substitution rule, so nothing is restated here. */}
            {zone.water.implication && (
              <li className="provenance">{zone.water.implication}</li>
            )}
            {zone.water.caveats.map((c) => (
              <li key={c} className="provenance">
                {c}
              </li>
            ))}
            <li className="provenance">{zone.water.resolution_note}</li>
            <li className="provenance">Source: {zone.water.source}</li>
            {m.aqi_attribution && (
              <li className="provenance">Air quality: {m.aqi_attribution}</li>
            )}
            <li className="provenance">{zone.placement_rationale}</li>
          </ul>
        </details>
      </section>

      {/* projected impact */}
      {zone.projected_impact && (
        <section className="zb-section">
          <span className="eyebrow">Projected impact</span>
          {zone.projected_impact.plant_count === 0 ? (
            <p className="zb-noimpact muted">
              No meaningful planting capacity here — the open ground is already
              vegetated, so additional planting would add little.
            </p>
          ) : (
            <>
              <dl className="zb-metrics">
                <div>
                  <dt>Plants</dt>
                  <dd className="tabular">
                    {zone.projected_impact.plant_count.toLocaleString()}
                  </dd>
                </div>
                <div>
                  <dt>CO₂ / yr</dt>
                  <dd className="tabular">
                    {zone.projected_impact.co2_tonnes_yr.toFixed(2)} t
                  </dd>
                </div>
                <div>
                  <dt>PM / yr</dt>
                  <dd className="tabular">
                    {zone.projected_impact.pm_deposition_kg_yr.toFixed(0)} kg
                  </dd>
                </div>
                <div>
                  <dt>At maturity</dt>
                  <dd className="tabular">
                    ~{zone.projected_impact.maturity_years} yrs
                  </dd>
                </div>
                {/* The same projection in rupees — it multiplies the plant count
                  * directly above, so it belongs in the same list rather than in a
                  * separate, more confident-looking block of its own. */}
                <div>
                  <dt>Est. cost</dt>
                  <dd className="tabular">
                    {formatINR(zone.implementation_cost.total_inr)}
                  </dd>
                </div>
              </dl>
            </>
          )}
          {/* One disclosure for both projections. Splitting them would let the cost
            * be read without the caveats that apply to the count it is built on. */}
          <details className="zb-gaps">
            <summary className="provenance">
              How this estimate works (
              {zone.projected_impact.caveats.length +
                zone.implementation_cost.caveats.length +
                1 + // cost basis line
                (zone.projected_impact.plant_count > 0 ? 2 : 0)}
              )
            </summary>
            <ul>
              {/* The two derivations sit at the top of the disclosure rather than
                * above it: they explain how the numbers were reached, which is the
                * same job the caveats do. */}
              {zone.projected_impact.plant_count > 0 && (
                <>
                  <li className="provenance">
                    Assuming {zone.projected_impact.species_name}:{" "}
                    {zone.projected_impact.capacity_basis}
                  </li>
                  <li className="provenance">
                    Cost: {zone.implementation_cost.basis}.
                    {!zone.implementation_cost.basis_matches_placement &&
                      " The published norm describes ground plantation, not this zone's placement."}
                  </li>
                </>
              )}
              {zone.projected_impact.caveats.map((c) => (
                <li key={c} className="provenance">
                  {c}
                </li>
              ))}
              {zone.implementation_cost.caveats.map((c) => (
                <li key={c} className="provenance">
                  {c}
                </li>
              ))}
              <li className="provenance">
                Cost basis: {zone.implementation_cost.source}.
              </li>
            </ul>
          </details>
        </section>
      )}

      {/* 4 — species shortlist */}
      <section className="zb-section">
        <span className="eyebrow">Suggested species</span>

        {loading && <p className="muted zb-loading">Running Model B…</p>}
        {error && <p className="zb-error">Couldn't load species: {error}</p>}

        {zone.species && (
          <ol className="zb-species">
            {zone.species.map((s, i) => (
              <li key={s.species_id}>
                <div className="zb-sp-head">
                  <span className="zb-sp-rank">{i + 1}</span>
                  <div>
                    <div className="zb-sp-name">{s.display_name}</div>
                    <div className="zb-sp-sci muted">{s.scientific_name}</div>
                  </div>
                  <span className="zb-sp-fit tabular faint">
                    {s.fit_score.toFixed(2)}
                  </span>
                </div>
                <ul className="zb-sp-qualities">
                  {s.key_qualities.map((q) => (
                    <li key={q}>{q}</li>
                  ))}
                </ul>
                {s.concerns.length > 0 && (
                  <ul className="zb-sp-concerns">
                    {s.concerns.map((c) => (
                      <li key={c}>{c}</li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>

      {/* 5 — crossbreeds */}
      <button
        type="button"
        className="btn zb-explore"
        onClick={() => onExploreCrossbreeds(zone)}
        disabled={!zone.species}
      >
        Explore crossbreeds
        <svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.7">
          <path d="M3 8h9M9 5l3 3-3 3" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {zone.data_gaps.length > 0 && (
        <details className="zb-gaps">
          <summary className="provenance">
            {zone.data_gaps.length} data gap{zone.data_gaps.length > 1 ? "s" : ""}
          </summary>
          <ul>
            {zone.data_gaps.map((g) => (
              <li key={g} className="provenance">
                {g}
              </li>
            ))}
          </ul>
        </details>
      )}
    </article>
  );
}
