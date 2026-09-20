/* API client and types mirroring the FastAPI payloads.
 *
 * Every measurement type here is nullable, deliberately. The backend reports a
 * missing signal as null with a matching entry in `data_gaps` rather than
 * substituting a value, and the UI has to render that state honestly — so the
 * types make "we don't know" unavoidable at the call site.
 */

const BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

export type Placement = "ground" | "wall_hanging" | "potted";
export type PriorityBand = "Critical" | "High" | "Moderate" | "Low";
export type ConfidenceBand = "Promising" | "Plausible" | "Unlikely" | "Not viable";

export interface ZoneMeasurements {
  aqi: number | null;
  aqi_attribution: string;
  pm25_ugm3: number | null;
  traffic_proxy: number | null;
  road_density_km_per_km2: number | null;
  floor_area_ratio: number | null;
  building_footprint_ratio: number | null;
  road_surface_ratio: number | null;
  plantable_ground_ratio: number | null;
  ndvi: number | null;
  land_surface_temp_c: number | null;
  heat_island_delta_c: number | null;
}

export interface Species {
  species_id: string;
  scientific_name: string;
  common_name: string;
  display_name: string;
  family: string;
  habit: string;
  is_cultivar: boolean;
  cultivar_epithet: string | null;
  fit_score: number;
  key_qualities: string[];
  concerns: string[];
  apti: number;
  apti_band: string;
  mature_height_m: number;
  co2_sequestration_kg_yr: number;
  evidence: string;
}

export interface StreetViewPhoto {
  available: boolean;
  status: string;
  path: string | null;
  captured: string | null;
  attribution: string;
  heading: number | null;
  pano_distance_m: number | null;
  detail: string | null;
}

export interface ProjectedImpact {
  plant_count: number;
  species_id: string;
  species_name: string;
  co2_kg_yr: number;
  co2_tonnes_yr: number;
  pm_deposition_kg_yr: number;
  cooling_kwh_yr: number;
  cars_equivalent: number;
  maturity_years: number;
  capacity_basis: string;
  caveats: string[];
}

/* CGWB groundwater status for the BLOCK (tehsil) containing a zone — never a
 * zone-level reading. `tehsil` and `resolution_note` are non-optional in practice so
 * the UI always has the means to say what the number actually describes. */
export interface WaterAvailability {
  available: boolean;
  tehsil: string | null;
  district: string | null;
  category: "safe" | "semi_critical" | "critical" | "over_exploited" | null;
  category_label: string | null;
  severity: number | null;
  match: "confirmed" | "approximate" | "unmapped";
  implication: string | null;
  resolution_note: string;
  source: string;
  assessment_year: number | null;
  caveats: string[];
}

/* Housing-type proxy, not income. `proxy_label` is carried on every payload
 * specifically so no render site can drop the qualifier. */
export interface EquitySignal {
  clusters_in_zone: number;
  clusters_within_range: number;
  range_m: number;
  tier: "substantial" | "some" | "none";
  tier_label: string;
  nearest_name: string | null;
  nearest_distance_m: number | null;
  examples: string[];
  proxy_label: string;
  source: string;
  published: string | null;
  caveats: string[];
}

export interface ImplementationCost {
  plant_count: number;
  per_sapling_inr: number;
  total_inr: number;
  field_cost_inr: number;
  admin_contingency_inr: number;
  maintenance_years: number;
  /* False when the zone's placement is not the ground plantation the published norm
   * describes — the UI shows the mismatch rather than hiding it behind the total. */
  basis_matches_placement: boolean;
  basis: string;
  source: string;
  caveats: string[];
}

/** Whether the priority ranking concentrates on one end of the housing record. */
export interface EquityCheck {
  applicable: boolean;
  top_n?: number;
  top_with_settlement?: number;
  rest_count?: number;
  rest_with_settlement?: number;
  verdict?: "favours_lower_income" | "favours_higher_income" | "even";
  summary: string;
  note?: string;
  proxy_label: string;
  range_m?: number;
}

export interface Zone {
  zone_id: string;
  name: string;
  locality: string;
  latitude: number;
  longitude: number;
  priority_score: number;
  priority_band: PriorityBand;
  placement: Placement;
  placement_rationale: string;
  confidence: number;
  measurements: ZoneMeasurements;
  vegetation: {
    has_existing_vegetation: boolean | null;
    label: string;
    ndvi_threshold: number;
  };
  signals: Record<string, number>;
  missing_signals: string[];
  sources: Record<string, string>;
  data_gaps: string[];
  selection_rationale: string;
  street_view: StreetViewPhoto;
  projected_impact: ProjectedImpact | null;
  implementation_cost: ImplementationCost;
  water: WaterAvailability;
  equity: EquitySignal;
  species?: Species[];
}

/** Absolute URL for a media path the API serves (Street View, generated images). */
export function mediaUrl(path: string): string {
  return path.startsWith("http") ? path : `${BASE}${path}`;
}

/** Sum projections across selected zones.
 *
 * Summing is linear and therefore optimistic — adjacent plantings overlap in
 * practice. The caveat travels with the total so the UI cannot show the number
 * without it.
 */
export function sumImpact(zones: Zone[]): {
  plant_count: number;
  co2_tonnes_yr: number;
  pm_deposition_kg_yr: number;
  cooling_kwh_yr: number;
  cars_equivalent: number;
  max_maturity_years: number;
  zones_with_capacity: number;
} {
  const items = zones
    .map((z) => z.projected_impact)
    .filter((i): i is ProjectedImpact => i !== null);

  const co2 = items.reduce((n, i) => n + i.co2_kg_yr, 0);
  return {
    plant_count: items.reduce((n, i) => n + i.plant_count, 0),
    co2_tonnes_yr: co2 / 1000,
    pm_deposition_kg_yr: items.reduce((n, i) => n + i.pm_deposition_kg_yr, 0),
    cooling_kwh_yr: items.reduce((n, i) => n + i.cooling_kwh_yr, 0),
    cars_equivalent: co2 / 2000,
    max_maturity_years: items.reduce((n, i) => Math.max(n, i.maturity_years), 0),
    zones_with_capacity: items.filter((i) => i.plant_count > 0).length,
  };
}

/* `failed` and `pending` are deliberately distinct: attempted-and-failed is an
 * operational problem to fix, not-yet-attempted is just unspent budget, and the UI
 * shows them differently. `detail` carries the reason on a failure. */
export interface PairingImage {
  status: "pending" | "ready" | "failed";
  path: string | null;
  prompt: string | null;
  detail?: string | null;
  generated_at: number | null;
}

export interface Pairing {
  pair_id: string;
  zone_id?: string;
  species_a: { id: string; name: string; scientific_name: string; is_cultivar?: boolean };
  species_b: { id: string; name: string; scientific_name: string; is_cultivar?: boolean };
  image?: PairingImage;
  confidence: number;
  confidence_low: number;
  confidence_high: number;
  band: ConfidenceBand;
  explanation: string;
  terms: {
    crossability: number;
    hybrid_evidence: number;
    parental_merit: number;
    complementarity: number;
    ploidy_factor: number;
  };
  divergence_mya: number | null;
  divergence_ci: [number, number] | null;
  divergence_studies: number;
  is_conspecific: boolean;
  crossability_from_rank: boolean;
  citations: string[];
  data_gaps: string[];
}

export interface PairingTier {
  label: string;
  note: string;
  pairings: Pairing[];
}

export interface ZonePairings {
  zone_id: string;
  /* "precomputed" means every drag is a dictionary lookup with no outbound call.
   * "live" means the store is missing and the demo is degraded — surfaced in the UI
   * rather than hidden, because slow-and-network-dependent is exactly the failure
   * the precompute exists to prevent. */
  source: "precomputed" | "live";
  generated_at: number | null;
  ambitious: PairingTier;
  safer: PairingTier;
}

/** Order-independent key, matching the backend's `_pair_id`. */
export function pairKey(aId: string, bId: string): string {
  return [aId, bId].sort().join("__");
}

export interface CityBaseline {
  aqi: number | null;
  aqi_band: string | null;
  aqi_standard: string;
  pm25_ugm3: number | null;
  pm10_ugm3: number | null;
  no2_ugm3: number | null;
  co_mgm3: number | null;
  station_count: number;
  stations: string[];
  core_stations_live: number;
  core_stations_expected: number;
  measured_at: string | null;
  source: string;
  zone_count: number;
  mean_priority: number | null;
}

export interface Health {
  ready: boolean;
  error: string | null;
  zones_scored: number;
  zones_total: number;
  missing_api_keys: string[];
  earth_engine_available: boolean;
  air_quality_available: boolean;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body — keep statusText */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<Health>("/api/health"),
  city: () => get<CityBaseline>("/api/city/delhi"),
  zones: () => get<{ zones: Zone[]; equity_check: EquityCheck }>("/api/zones"),
  zone: (id: string) => get<Zone>(`/api/zones/${id}`),
  pairings: (id: string) => get<ZonePairings>(`/api/zones/${id}/pairings`),
};

/* --- display helpers ----------------------------------------------------- */

export const PLACEMENT_LABEL: Record<Placement, string> = {
  ground: "Ground",
  wall_hanging: "Wall-hanging",
  potted: "Potted",
};

export const PRIORITY_VAR: Record<PriorityBand, string> = {
  Critical: "var(--priority-critical)",
  High: "var(--priority-high)",
  Moderate: "var(--priority-moderate)",
  Low: "var(--priority-low)",
};

export const BAND_VAR: Record<ConfidenceBand, string> = {
  Promising: "var(--band-promising)",
  Plausible: "var(--band-plausible)",
  Unlikely: "var(--band-unlikely)",
  "Not viable": "var(--band-not-viable)",
};

/** Render a nullable measurement without ever printing "null" or a fake zero. */
export function fmt(
  value: number | null | undefined,
  digits = 1,
  suffix = "",
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(digits)}${suffix}`;
}

export function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}

/** Rupees in the lakh/crore scale Indian budgets are actually read in.
 *
 * Rounded to two significant-ish figures on purpose: this is a first-order estimate
 * built by multiplying two other estimates, and printing it to the rupee would imply
 * a precision the underlying unit cost does not have.
 */
export function formatINR(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (value === 0) return "₹0";
  if (value >= 1e7) return `₹${(value / 1e7).toFixed(2)} cr`;
  if (value >= 1e5) return `₹${(value / 1e5).toFixed(1)} L`;
  return `₹${Math.round(value).toLocaleString("en-IN")}`;
}

export const WATER_VAR: Record<string, string> = {
  over_exploited: "var(--priority-critical)",
  critical: "var(--priority-high)",
  semi_critical: "var(--priority-moderate)",
  safe: "var(--priority-low)",
};

/** Sum estimated implementation cost across selected zones. */
export function sumCost(zones: Zone[]): {
  total_inr: number;
  /* Zones whose placement is not the ground plantation the published norm describes.
   * Surfaced so a total spanning mixed placements says so. */
  mismatched_placements: number;
} {
  const items = zones.map((z) => z.implementation_cost).filter(Boolean);
  return {
    total_inr: items.reduce((n, c) => n + c.total_inr, 0),
    mismatched_placements: items.filter((c) => !c.basis_matches_placement).length,
  };
}

/** Housing-record spread across the live selection.
 *
 * Counts tiers the backend already assigned — the thresholds that produce those tiers
 * live in the backend beside the dataset, so this stays presentation only and cannot
 * drift from them.
 */
export function selectionEquity(zones: Zone[]): {
  withSettlement: number;
  total: number;
  summary: string;
} | null {
  const items = zones.map((z) => z.equity).filter(Boolean);
  if (items.length < 2) return null;

  const withSettlement = items.filter((e) => e.tier !== "none").length;
  const total = items.length;
  const without = total - withSettlement;
  const km = `${(items[0].range_m / 1000).toFixed(1).replace(/\.0$/, "")} km`;

  let summary: string;
  if (withSettlement === 0) {
    summary = `None of these ${total} zones has a recorded J.J. basti within ${km} — this selection is drawn entirely from areas the housing record does not flag as informal settlement.`;
  } else if (without === 0) {
    summary = `All ${total} of these zones have recorded informal settlement nearby — no higher-income comparison is in the selection.`;
  } else {
    summary = `${withSettlement} of ${total} zones have recorded informal settlement nearby, ${without} ${
      without === 1 ? "does" : "do"
    } not.`;
  }
  return { withSettlement, total, summary };
}
