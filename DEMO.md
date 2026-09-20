# VeggieDelhi — demo cheat sheet

Memorise these. Do not drag live and hope: 73% of all pairings score "Not viable"
(that is the real biology, see below), so a random drag will usually land on a
rejection before you have set it up.

All figures below are read from `backend/data/static/crossbreeds.json`. Regenerate
with `.venv/bin/python scripts/precompute_crossbreeds.py` and re-check these numbers
if the catalogue or scoring changes.

---

## Before you start

```bash
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000   # API
cd frontend && npm run dev                                             # UI :5173
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
```

Confirm in the health payload:

- `ready: true`
- `crossbreeds_precomputed: true` — every drag is a dictionary lookup, no outbound call
- `precomputed_pairings: 128`
- `earth_engine_available: true` — NDVI and surface temperature are live

If `crossbreeds_precomputed` is false the popup still works but computes live against
NCBI/TimeTree/GBIF. Responses are tagged `"source": "live"`. Do not demo in that state.

Then confirm the before-photograph actually resolves — it is fetched live, not bundled:

```bash
curl -s http://127.0.0.1:8000/api/zones/rk-puram | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["street_view"])'
```

Expect `"available": true` and `"status": "OK"`. A `REQUEST_DENIED` here almost always
means a stale `GOOGLE_API_KEY` in the environment is shadowing `.env` — `config.py`
loads `.env` with `override=False`, so a real env var wins. Check with
`echo $GOOGLE_API_KEY` and unset it if it does not match `.env`. All 10 zones have
coverage; re-fetch with `.venv/bin/python scripts/fetch_streetview.py`.

---

## The zone: R K Puram Sector 12

Potted placement, priority 0.451 (Moderate, rank 6 of 10). Both demo pairs live in
this one zone, so there is no zone-switching mid-demo.

Five draggable species: **Kamini · Kaner · Yellow Bells · Snake Plant · Golden Dewdrop**

Chosen over the higher-ranked Mayapuri for one reason: the before-photograph. Mayapuri's
panorama sits 7 m from the centroid and frames a parked truck across most of the image.
R K Puram's is a Google panorama from April 2026 showing street, sky and existing canopy
— the "before" actually reads as a place you could plant. Mayapuri remains as a backup
below, and its pairing scores are a hair higher (0.610 vs 0.600) if you need them.

Expect a question about the borderline call — see caveats: R K Puram is 33% plantable
against a 35% ground threshold, which is why it is potted rather than ground. That is
worth volunteering rather than defending.

R K Puram is also one of the two zones where satellite NDVI contradicted our own
curation — we had it down as bare, the measurement came back vegetated, and we kept
the measurement. Worth saying out loud: the demo zone is one we were wrong about.

---

## 1. Lead with "Promising"

**Drag: Kaner 'Hardy Red' + Kaner 'Petite Pink'** (safer tier)

| | |
|---|---|
| Score | **0.600** (band 0.44–0.76) |
| Band | **Promising** — the highest available in this zone |
| Divergence | 0.0 Mya (same species) |
| Crossability | 1.00 |

On-screen text ends with: *"it will not unlock traits neither parent already has."*

Pause there. That is the model declining to oversell its own best result — it says
the reliable cross is also the unambitious one.

Cites: within-species cross; cultivar traits from horticultural trade literature.

---

## 2. Then "Not viable"

**Drag: Kaner + Snake Plant** (ambitious tier — Kaner is already on screen)

| | |
|---|---|
| Score | **0.000** |
| Band | **Not viable** |
| Divergence | **159.6 Mya**, across **48 studies** |
| Ploidy | 2n=22 vs 2n=36 |

Gives two independent reasons, not one: the divergence is far past the range where
plant hybridisation is realistic, *and* the chromosome counts differ so even a
successful cross would probably be sterile.

Cites: TimeTree 159.6 Mya (48 studies); GBIF records no hybrid taxa linking *Nerium*
to *Dracaena*.

---

## Why this ordering

Kaner appears in **both** drags. The only thing that changes is its partner, and the
score moves 0.600 → 0.000. The contrast is attributable to biology rather than to
switching examples.

Say "48 studies" out loud: the refusal is backed by more published evidence than the
acceptance is.

The bimodal distribution is the point, not a gap:

| Band | Count |
|---|---|
| Not viable | 93 |
| Promising | 28 |
| Unlikely | 5 |
| Plausible | 2 |

Cross-species crosses fail and cultivar crosses work. A tool that returned a
comfortable mid-range score for every pairing would be telling you nothing.

---

## Backups (same structure, if R K Puram misbehaves)

| Zone | Promising | Not viable |
|---|---|---|
| Mayapuri Industrial Area (potted) | Kaner 'Hardy Red' × 'Petite Pink' — **0.610** | Kaner × Snake Plant — **0.000**, 159.6 Mya |
| Ajmal Khan Road, Karol Bagh (wall-hanging) | Paper Flower 'Sanderiana' × 'Variegata' — **0.603** | Great Bougainvillea × Money Plant — **0.000**, 159.6 Mya |

Mayapuri is the closest substitute — identical five species, identical script, only the
photograph and the third decimal differ. Karol Bagh's photo has identifiable faces in
the foreground; prefer Mayapuri if you have to switch.

---

## Numbers worth having ready

- City AQI **133** (Moderate), CPCB National AQI, **54 live reference-grade stations**
- Priority spread **0.192** (Lodhi Garden) to **0.616** (Anand Vihar)
- Measured urban heat island: **37.0 °C** (Lodhi Garden) to **43.3 °C** (Okhla) — a
  real 6.3 °C spread
- Karol Bagh is **1% plantable ground** (39% built, 60% road surface) — which is why
  Model A puts it on walls
- Satellite NDVI contradicted our own curation on 2 of 10 zones. We kept the
  measurement and changed our claim, rather than the reverse.

## Known caveats (say these before someone asks)

- Road surface can double-count where OSM tags a footway alongside a carriageway, so
  Karol Bagh's 60% is likely somewhat inflated. The conclusion (no plantable ground)
  holds.
- R K Puram sits at 33% plantable against a 35% threshold — a genuinely borderline
  potted-vs-ground call.
- Trait/APTI values are literature-derived constants, not live reads. If asked for the
  paper behind a specific APTI decimal, the defensible claim is the tolerance *band*.
- Cultivar crossability is definitional (same species), not measured from divergence
  data. It is recorded as a data gap on every cultivar pairing.
- Impact projection is a first-order estimate from published i-Tree coefficients. It
  does not model diminishing returns.
