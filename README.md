<div align="center">

<img src="title.png" alt="VeggieDelhi" width="520" />

**Where should Delhi plant, what should it plant there, and what could those plants become?**

Ten real street-scale zones, scored on live air, satellite, and street data —
then matched to species, and to each other, using published divergence times.

<sub>🌱 **Submitted to HackMIT 2026** 🌱</sub>

![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![Vite](https://img.shields.io/badge/Vite-8-646CFF?logo=vite&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-6-3178C6?logo=typescript&logoColor=white)
![Earth Engine](https://img.shields.io/badge/Google%20Earth%20Engine-4285F4?logo=googleearth&logoColor=white)

</div>

---

## The problem

Delhi's greening budget is real, and so is the question of where to spend it. The
places that would benefit most from planting are usually the places that are hardest
to plant in — the arterial corridors, the industrial estates, the market streets where
there is no soil left at all. Deciding by eye tends to put trees where trees already
grow.

**VeggieDelhi** answers three questions in sequence, and shows its work for each:

| | Question | Answered by |
|---|---|---|
| 1 | **Where** should we plant, and can we physically plant there? | **Model A** — zone scoring |
| 2 | **What** belongs on that specific site? | **Model B** — trait shortlist |
| 3 | **What could those species become** if crossed? | **Model B** — compatibility scoring |

Every number in the interface carries its source. Every number that could not be
measured says so, by name, instead of quietly becoming a zero.

---

## How it works

<div align="center">
<img src="logo.png" alt="" width="110" />
</div>

### Model A — zone priority and placement

Five need signals, each normalised to 0–1, each meaning *"how much would planting here
help"*:

```
pollution burden   0.30   measured AQI at the nearest live monitor (CPCB scale)
heat load          0.22   zone LST minus the city median — relative heat island
vegetation deficit 0.22   1 − NDVI, so bare ground scores high and parks score low
traffic load       0.16   class-weighted OSM road density (a proxy, not a count)
exposure           0.10   built floor-area ratio — who benefits per m² planted
```

**Missing data is not zero.** If Earth Engine is unavailable there is no NDVI and no
LST — and scoring those as zero would rank every zone as maximally bare *and*
maximally cool at the same time, confidently wrong in both directions. Instead the
remaining weights are renormalised, the score records which inputs it used, and
`confidence` drops in proportion to what is missing.

**Placement is physical feasibility, not preference.** From measured building
footprint and road surface, each zone resolves to `ground`, `wall_hanging`, or
`potted`, with the surface breakdown returned as the rationale. The 35% plantable-ground
threshold was calibrated against a real failure: an earlier version tested `1 −
footprint` and put all ten zones in "ground", including Connaught Place's colonnaded
commercial core — because it counted every road, pavement and plaza as soil.

### Model B — species shortlist and cross-compatibility

Stage 1 narrows a 24-species / 14-cultivar Delhi catalogue to the five species that
actually fit the zone: a hard filter on physical feasibility (no 25 m banyan on a
wall), then a weighted trait fit using weights the zone's own measurements produce.

Stage 2 scores every pairing within that shortlist. The method translates the `gpcp` /
`predhy` R packages — genomic prediction of cross performance — into the data
available for urban tree species:

| gpcp / predhy | → | VeggieDelhi |
|---|---|---|
| parental GEBV for the target trait | → | per-parent trait fitness for the zone |
| genomic relationship matrix | → | **TimeTree** divergence time (live, published) |
| training-population validation | → | **GBIF** named hybrid taxa (recorded crosses) |
| *(assumed within a breeding pool)* | → | explicit crossability + ploidy screening |

That last row is the load-bearing addition. GBLUP-family methods assume the parents are
already crossable — safe for maize inbreds, unsafe across urban tree species. So
crossability is a **multiplicative gate**: two species that almost certainly cannot
cross score near zero no matter how well their traits complement each other.

The result is a deliberately bimodal distribution across the 128 precomputed pairings:

| Band | Count |
|---|---|
| Not viable | 93 |
| Promising | 28 |
| Unlikely | 5 |
| Plausible | 2 |

Cross-species crosses fail; cultivar crosses work. **A tool that returned a comfortable
mid-range score for every pairing would be telling you nothing.** Kaner × Snake Plant
scores 0.000 — 159.6 Mya apart across 48 studies, with mismatched chromosome counts.
The refusal is backed by more published evidence than most acceptances are.

### And then, what it would actually look like

Each pairing carries a generated image composited onto **that zone's real Street View
photograph**, grounded on open-licence GBIF reference photos of both parents. A
generated picture of a shrub on a neutral background says nothing about whether the
planting suits R K Puram Sector 12; a picture of R K Puram Sector 12 *with the shrub in
it* is the claim the tool is actually making.

---

## Live data sources

Nothing that can change is hardcoded.

| Source | Supplies | Notes |
|---|---|---|
| **OpenAQ v3** | AQI, PM2.5 | 54 live reference-grade stations. Liveness decided by each station's own `datetimeLast`, never by name — OpenAQ carries duplicate names where one ID is live and the other died in 2018. Converted to AQI via **CPCB** National AQI breakpoints, not the US EPA scale. |
| **Sentinel-2** (Earth Engine) | NDVI | 10 m surface reflectance → the vegetation signal per zone. |
| **Landsat 8/9** (Earth Engine) | Land surface temperature | Heat-island delta = zone LST − city median. Measured spread: **37.0 °C → 43.3 °C**. |
| **OpenStreetMap / Overpass** | Road density, building floor-space | Class-weighted; retried, mirrored, and disk-cached, because Overpass 504s under load. |
| **TimeTree** | Divergence times | CI width *and* study count both carried into the score as an explicit uncertainty term. |
| **GBIF** | Hybrid taxa, taxonomy, reference photos | The brief specified GRIN-Global, which publishes no JSON API; GBIF exposes the same class of evidence machine-readably. |
| **NCBI Taxonomy** | Name → taxid | Exists because TimeTree's pairwise endpoint accepts only numeric taxids. |
| **Google Street View Static** | The "before" photograph | Metadata checked first (free) so an uncovered zone never triggers a billable call. Heading is *computed* from the panorama toward the zone centroid, not defaulted to north. |
| **DUSIB** | Equity proxy | Spatial join against 675 published J.J. basti coordinates. A housing-type proxy — **never** an income figure, and every payload says so. |
| **CGWB** | Groundwater stress | Reported at tehsil resolution, with a note saying so, because nothing here will invent a per-zone water figure. |
| **GNCTD Forests & Wildlife** | Cost | ₹5,950/sapling compensatory-plantation norm, including 10 years of maintenance. |

---

## Quick start

**Requirements:** Python 3.14+, Node 20+, and API keys (see `.env.example`).

```bash
# 1. Backend
python3 -m venv .venv
.venv/bin/pip install fastapi uvicorn httpx python-dotenv earthengine-api

# 2. Secrets — .env is gitignored; real env vars win over the file
cp .env.example .env && $EDITOR .env

# 3. Prove the upstreams are actually live before trusting anything
.venv/bin/python scripts/verify_sources.py

# 4. Precompute the crossbreed store (scores only — free, ~70s)
.venv/bin/python scripts/precompute_crossbreeds.py

# 5. Run
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
cd frontend && npm install && npm run dev          # → http://localhost:5173
```

Confirm the system is demo-ready:

```bash
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
```

You want `ready: true`, `earth_engine_available: true`, and
`crossbreeds_precomputed: true` with `precomputed_pairings: 128` — that last one is the
guarantee that **every drag in the UI is a dictionary lookup with no outbound call.**

---

## API

| Endpoint | Returns |
|---|---|
| `GET /api/health` | What is actually reachable right now — missing keys, upstream availability, precompute state. |
| `GET /api/city/delhi` | City-wide air-quality baseline and mean priority. |
| `GET /api/zones` | All 10 zones, priority-ranked, each with measurements, sources, data gaps, projected impact, cost, water, and equity — plus an `equity_check` on whether the ranking as a whole leans toward one end of the housing spectrum. |
| `GET /api/zones/{id}` | One zone, including its Model B species shortlist. |
| `GET /api/zones/{id}/pairings` | Both pairing tiers. Tagged `"source": "precomputed"` or `"live"`, so a degraded demo is *visible* rather than merely slow. |
| `GET /api/species` | The full trait catalogue, with per-entry evidence. |

---

## Repository layout

```
backend/
  app/
    main.py              FastAPI — fetches, serialises, caches. No scoring logic lives here.
    zones.py             The 10 curated Delhi zones (selection is curated; measurements are not)
    config.py            Secrets from env only; station pinning; CPCB constants
    sources/             openaq · overpass · gee · timetree · gbif · ncbi · streetview
                         groundwater · equity · http_cache (retry + mirror + disk cache)
  models/
    model_a_zone_scoring/    priority score + placement category
    model_b_genomic/         shortlist · compatibility · species catalogue
    impact_projection.py     i-Tree coefficients × measured capacity
    cost_estimate.py         published GNCTD unit rate × plant count
  precompute/
    crossbreeds.py           every pairing, scored ahead of time
    pairing_images.py        composited onto each zone's real photograph

frontend/src/
  pages/Landing.tsx          city selector
  pages/Dashboard.tsx        map + selected-zone panel (selection order is the state of record)
  components/Map/            Leaflet — colour = priority, glyph = placement (two channels,
                             so the ramp stays readable without colour vision)
  components/ZoneBlock/      per-zone: before photo, NDVI signal, top 5 species, impact, cost
  components/CrossbreedModal/  drag-to-cross popup — pointer events, fully keyboard-reachable

scripts/                     verify_sources · test_model_a · test_model_b
                             precompute_crossbreeds · generate_pairing_images
                             fetch_streetview · fetch_jj_bastis · plan_precompute
                             check_score_ceiling
```

Model A never imports Model B, and Model B never imports Model A. Model A emits a
`ZoneProfile`; Model B consumes one. Either can be tested or replaced on its own.

---

## Design principles

> **A number without a source is a bug.**

1. **Missing data is missing, not zero.** Renormalise the weights, name the gap, drop
   the confidence.
2. **Curation is disclosed; measurement is not curated.** *Which* 10 zones appear is a
   stated demo choice. Every measurement attached to them is fetched live.
3. **The model is allowed to disagree with us.** Satellite NDVI contradicted our own
   curation on 2 of 10 zones. We kept the measurement and changed our claim.
4. **Proxies are named as proxies.** Road density is a traffic *proxy*. Basti
   proximity is a housing-type *proxy*, never income. Groundwater is *tehsil*-wide.
5. **Caveats travel with the number.** Every projection carries its `caveats` array
   through the API into the UI, so a figure cannot reach a user stripped of its limits.
6. **The demo path must not depend on a third party being up.** All 128 pairings and
   their images are precomputed to disk; the live path exists only as a fallback and
   marks itself as one.

---

## Known limitations

Stated here rather than waiting to be found:

- **Impact projection is linear and at maturity.** The tenth tree on a street removes
  less marginal PM than the first; nothing models that. It also assumes survival, so
  treat it as a ceiling on a successful planting rather than an expected value.
- **Cost inherits the plant count's error.** It is a published unit rate × a
  first-order capacity estimate. Two estimates multiplied compound.
- **The Delhi norm describes ground plantation.** No published Delhi unit rate was
  found for container or facade planting, so those zones report the ground norm with
  the mismatch stated rather than a silently invented rate.
- **Road surface can double-count** where OSM tags a footway alongside a carriageway.
  Karol Bagh's 60% is likely inflated; the conclusion (no plantable ground) holds.
- **APTI values are literature-derived constants**, not live reads. They vary between
  studies by site and season, so the tolerance *band* is the load-bearing claim, not
  the decimal.
- **Cultivar crossability is definitional** (same species), not measured from
  divergence data — recorded as a data gap on every cultivar pairing.
- **Predicted compatibility is a prior**, not a claim that a cross has been performed,
  will succeed, or will be fertile.

---

## Credits

Built for **HackMIT 2026**.

Air quality from OpenAQ, converted via CPCB breakpoints · Imagery from Sentinel-2 and
Landsat 8/9 via Google Earth Engine · Road and building data © OpenStreetMap
contributors (ODbL) · Divergence times from TimeTree · Hybrid records and CC-licensed
photographs from GBIF and iNaturalist · Taxonomy from NCBI · Street View imagery ©
Google, served unmodified to preserve attribution · Settlement coordinates from DUSIB ·
Groundwater categories from CGWB · Unit costs from the GNCTD Department of Forests &
Wildlife.

Methodology adapted from the `gpcp` and `predhy` genomic cross-prediction packages, and
i-Tree Eco impact coefficients. APTI after Singh & Rao (1983).

<div align="center">
<sub>See <a href="DEMO.md">DEMO.md</a> for the walkthrough script and the numbers worth having ready.</sub>
</div>
