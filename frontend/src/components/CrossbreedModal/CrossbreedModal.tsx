import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  BAND_VAR,
  api,
  mediaUrl,
  pairKey,
  type Pairing,
  type Species,
  type Zone,
  type ZonePairings,
} from "../../lib/api";
import "./CrossbreedModal.css";

/* The crossbreed popup.
 *
 * HARD CONSTRAINT: dragging never triggers generation or scoring. All pairings are
 * fetched once when the modal opens and held in a Map keyed by the same
 * order-independent pair id the backend uses, so a drop is a synchronous lookup.
 * If the lookup misses, the UI says so plainly rather than falling back to a
 * network call — a miss is a precompute bug, and hiding it behind a live request is
 * how a demo dies on stage.
 *
 * Drag uses pointer events rather than HTML5 drag-and-drop. HTML5 DnD cannot render
 * a custom drag image reliably across browsers, has no touch support, and fires
 * dragover at unpredictable rates. Pointer events give one code path for mouse and
 * touch, and let the dragged chip be a real styled element.
 *
 * Keyboard path is not an afterthought: every species is a button, and selecting two
 * in sequence produces the same result as a drag. A drag-only interaction would make
 * the entire feature unreachable by keyboard.
 */

type Slot = "a" | "b";

interface Slots {
  a: Species | null;
  b: Species | null;
}

/* `active` separates "the pointer is down on a chip" from "the user is dragging it".
 * A plain click is pointerdown + pointerup with no movement between, and treating
 * that as a drag is what made clicking a species behave differently from dragging
 * one. The ghost only appears, and the drop only resolves, once `active` is true. */
interface DragState {
  species: Species;
  startX: number;
  startY: number;
  active: boolean;
}

/* Pointer travel, in px, before a press counts as a drag rather than a click.
 * Generous enough to absorb the few pixels of jitter a mouse or finger produces
 * during an ordinary tap. */
const DRAG_THRESHOLD_PX = 6;

/** Put `species` in `slot`, moving it out of the other slot if it is already there. */
function place(slots: Slots, species: Species, slot: Slot): Slots {
  const other: Slot = slot === "a" ? "b" : "a";
  const next: Slots = { ...slots, [slot]: species };
  if (slots[other]?.species_id === species.species_id) next[other] = null;
  return next;
}

/** Keyboard/click path: fill the first empty slot, otherwise replace the second. */
function placeNext(slots: Slots, species: Species): Slots {
  if (!slots.a) return place(slots, species, "a");
  if (!slots.b) return place(slots, species, "b");
  return place(slots, species, "b");
}

export default function CrossbreedModal({
  zone,
  onClose,
}: {
  zone: Zone;
  onClose: () => void;
}) {
  const [data, setData] = useState<ZonePairings | null>(null);
  const [error, setError] = useState<string | null>(null);
  /* Both parents live in ONE state object so every update is a single functional
   * transition that can see both slots at once. Two separate useStates meant any
   * handler reading `slotA` to decide where `slotB` should go was reading a render
   * snapshot, so two assignments landing in the same batch both saw the empty
   * starting state and the second silently overwrote the first. */
  const [slots, setSlots] = useState<Slots>({ a: null, b: null });
  const [revealed, setRevealed] = useState(false);

  const [drag, setDrag] = useState<DragState | null>(null);
  const [dragPos, setDragPos] = useState({ x: 0, y: 0 });
  const [hoverSlot, setHoverSlot] = useState<Slot | null>(null);
  /* Set when a real drag ends, so the synthetic click the browser fires afterwards
   * does not get treated as a second, independent selection. Cleared on the next
   * pointerdown so it can never leak into an unrelated interaction. */
  const suppressClick = useRef(false);

  const slotA = slots.a;
  const slotB = slots.b;

  const slotARef = useRef<HTMLDivElement>(null);
  const slotBRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  /* --- load once ---------------------------------------------------------- */
  useEffect(() => {
    let alive = true;
    api
      .pairings(zone.zone_id)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(String(e.message ?? e)));
    return () => {
      alive = false;
    };
  }, [zone.zone_id]);

  /* Every pairing, both tiers, keyed for O(1) lookup on drop. */
  const lookup = useMemo(() => {
    const map = new Map<string, Pairing>();
    if (!data) return map;
    for (const p of [...data.ambitious.pairings, ...data.safer.pairings]) {
      map.set(pairKey(p.species_a.id, p.species_b.id), p);
    }
    return map;
  }, [data]);

  const result = useMemo(() => {
    if (!slotA || !slotB || slotA.species_id === slotB.species_id) return null;
    return lookup.get(pairKey(slotA.species_id, slotB.species_id)) ?? null;
  }, [slotA, slotB, lookup]);

  /* Reset the reveal, then re-trigger it on the next frame so the --score
   * transition actually runs instead of being collapsed by React's batching. */
  useEffect(() => {
    setRevealed(false);
    if (!result) return;
    const id = requestAnimationFrame(() => setRevealed(true));
    return () => cancelAnimationFrame(id);
  }, [result]);

  /* --- escape + focus trap ------------------------------------------------- */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  /* --- drag ---------------------------------------------------------------
   *
   * Movement, drop resolution and drag teardown are all bound to the WINDOW rather
   * than to the modal element. Binding them to the modal meant a release anywhere
   * outside it — over the backdrop, or past the edge of the window — never cleared
   * the drag, so the ghost kept following the cursor and the next pointerup dropped
   * the abandoned species instead of the one just picked up. */
  const slotUnder = useCallback((x: number, y: number): Slot | null => {
    for (const [slot, ref] of [["a", slotARef], ["b", slotBRef]] as const) {
      const r = ref.current?.getBoundingClientRect();
      if (r && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) {
        return slot as Slot;
      }
    }
    return null;
  }, []);

  const onPointerDown = (e: React.PointerEvent, species: Species) => {
    if (e.button !== 0 && e.pointerType === "mouse") return;
    suppressClick.current = false;
    // Armed, not yet dragging: a press that never moves stays a click.
    setDrag({ species, startX: e.clientX, startY: e.clientY, active: false });
    setDragPos({ x: e.clientX, y: e.clientY });
  };

  useEffect(() => {
    if (!drag) return;

    const onMove = (e: PointerEvent) => {
      setDragPos({ x: e.clientX, y: e.clientY });
      const far =
        Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) >
        DRAG_THRESHOLD_PX;
      if (far && !drag.active) setDrag((d) => (d ? { ...d, active: true } : d));
      if (far || drag.active) setHoverSlot(slotUnder(e.clientX, e.clientY));
    };

    const onUp = (e: PointerEvent) => {
      // A press that never became a drag is left entirely to the click handler.
      // Resolving it here as well is what caused one click to be counted twice.
      if (drag.active) {
        const slot = slotUnder(e.clientX, e.clientY);
        const r = dialogRef.current?.getBoundingClientRect();
        const insideModal =
          !!r &&
          e.clientX >= r.left && e.clientX <= r.right &&
          e.clientY >= r.top && e.clientY <= r.bottom;

        if (slot) {
          setSlots((s) => place(s, drag.species, slot));
        } else if (insideModal) {
          // Missed the slot but stayed in the dialog — treat it as an imprecise
          // drop and fill the first empty slot rather than silently doing nothing.
          setSlots((s) => placeNext(s, drag.species));
        }
        // Released outside the dialog entirely: that reads as abandoning the drag,
        // so nothing is assigned. Either way the drag state is torn down below.
        suppressClick.current = true;
      }
      setDrag(null);
      setHoverSlot(null);
    };

    const onCancel = () => {
      setDrag(null);
      setHoverSlot(null);
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onCancel);
    // A drag interrupted by the tab losing focus never receives a pointerup at all.
    window.addEventListener("blur", onCancel);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
      window.removeEventListener("blur", onCancel);
    };
  }, [drag, slotUnder]);

  /* Keyboard/click equivalent: fills the first empty slot, then replaces B. */
  const onSpeciesActivate = (species: Species) => {
    if (suppressClick.current) {
      // The click the browser emits after a completed drag. Already handled.
      suppressClick.current = false;
      return;
    }
    setSlots((s) => placeNext(s, species));
  };

  const clear = () => setSlots({ a: null, b: null });

  const species = zone.species ?? [];
  const cultivarPairs = data?.safer.pairings ?? [];

  return (
    <div className="cb-scrim" onClick={onClose} role="presentation">
      <div
        ref={dialogRef}
        className="cb-modal"
        role="dialog"
        aria-modal="true"
        aria-label={`Explore crossbreeds for ${zone.name}`}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="cb-head">
          <div>
            <span className="eyebrow">Explore crossbreeds</span>
            <h2 className="cb-title">{zone.name}</h2>
          </div>
          <div className="cb-head-right">
            {data && (
              <span
                className={`pill ${data.source === "precomputed" ? "pill-live" : "pill-caution"}`}
                title={
                  data.source === "precomputed"
                    ? "All pairings precomputed — dragging performs a local lookup"
                    : "Precomputed store missing — scored live, slower and network-dependent"
                }
              >
                {data.source === "precomputed" ? "Precomputed" : "Live (degraded)"}
              </span>
            )}
            <button type="button" className="cb-close" onClick={onClose} aria-label="Close">
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7">
                <path d="M4 4l8 8M12 4l-8 8" strokeLinecap="round" />
              </svg>
            </button>
          </div>
        </header>

        {error && <p className="cb-error">Couldn't load pairings: {error}</p>}

        <div className="cb-body">
          {/* --- left: species tray + slots --------------------------------- */}
          <div className="cb-left">
            <p className="cb-instruction">
              Drag two species together — or select them with the keyboard — to see a
              predicted compatibility.
            </p>

            <div className="cb-slots">
              <Slot
                innerRef={slotARef}
                species={slotA}
                active={hoverSlot === "a"}
                label="First parent"
                onClear={() => setSlots((s) => ({ ...s, a: null }))}
              />
              <div className="cb-cross" aria-hidden="true">×</div>
              <Slot
                innerRef={slotBRef}
                species={slotB}
                active={hoverSlot === "b"}
                label="Second parent"
                onClear={() => setSlots((s) => ({ ...s, b: null }))}
              />
            </div>

            {(slotA || slotB) && (
              <button type="button" className="cb-clear" onClick={clear}>
                Clear
              </button>
            )}

            <span className="eyebrow cb-tray-label">
              Shortlisted species — distinct species
            </span>
            <ul className="cb-tray">
              {species.map((s) => (
                <li key={s.species_id}>
                  <button
                    type="button"
                    className={`cb-chip${
                      drag?.active && drag.species.species_id === s.species_id
                        ? " is-dragging"
                        : ""
                    }`}
                    onPointerDown={(e) => onPointerDown(e, s)}
                    onClick={() => onSpeciesActivate(s)}
                  >
                    <span className="cb-chip-name">{s.display_name}</span>
                    <span className="cb-chip-sci">{s.scientific_name}</span>
                  </button>
                </li>
              ))}
            </ul>

            {cultivarPairs.length > 0 && (
              <>
                <span className="eyebrow cb-tray-label">
                  Higher-confidence alternatives — same-species cultivars
                </span>
                <p className="provenance cb-tier-note">{data?.safer.note}</p>
                <ul className="cb-tray cb-tray-cultivar">
                  {cultivarPairs.map((p) => (
                    <li key={p.pair_id}>
                      <button
                        type="button"
                        className="cb-cultivar-pair"
                        onClick={() => {
                          const a = { species_id: p.species_a.id, display_name: p.species_a.name, scientific_name: p.species_a.scientific_name } as Species;
                          const b = { species_id: p.species_b.id, display_name: p.species_b.name, scientific_name: p.species_b.scientific_name } as Species;
                          setSlots({ a, b });
                        }}
                      >
                        <span
                          className="cb-cultivar-dot"
                          style={{ background: BAND_VAR[p.band] }}
                          aria-hidden="true"
                        />
                        <span>
                          {p.species_a.name} <span className="faint">×</span>{" "}
                          {p.species_b.name}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>

          {/* --- right: result ----------------------------------------------- */}
          <div className="cb-right">
            {!result ? (
              <div className="cb-placeholder">
                <p className="muted">
                  {slotA || slotB
                    ? "Add a second species to see the predicted compatibility."
                    : "No pairing selected yet."}
                </p>
              </div>
            ) : (
              <Result pairing={result} revealed={revealed} />
            )}

            {slotA && slotB && !result && (
              <p className="cb-miss">
                No precomputed result for this pairing. That's a precompute gap, not a
                live-scoring opportunity — re-run{" "}
                <code>scripts/precompute_crossbreeds.py</code>.
              </p>
            )}
          </div>
        </div>

        {/* Drag ghost follows the pointer — only once the press has actually become
          * a drag, so a plain click never flashes one. */}
        {drag?.active && (
          <div
            className="cb-ghost"
            style={{ left: dragPos.x, top: dragPos.y }}
            aria-hidden="true"
          >
            {drag.species.display_name}
          </div>
        )}
      </div>
    </div>
  );
}

/* --- sub-components -------------------------------------------------------- */

function Slot({
  innerRef,
  species,
  active,
  label,
  onClear,
}: {
  innerRef: React.RefObject<HTMLDivElement | null>;
  species: Species | null;
  active: boolean;
  label: string;
  onClear: () => void;
}) {
  return (
    <div
      ref={innerRef}
      className={`cb-slot${active ? " is-active" : ""}${species ? " is-filled" : ""}`}
    >
      {species ? (
        <>
          <span className="cb-slot-name">{species.display_name}</span>
          <span className="cb-slot-sci">{species.scientific_name}</span>
          <button type="button" className="cb-slot-clear" onClick={onClear} aria-label={`Remove ${species.display_name}`}>
            ×
          </button>
        </>
      ) : (
        <span className="cb-slot-empty">{label}</span>
      )}
    </div>
  );
}

function Result({ pairing, revealed }: { pairing: Pairing; revealed: boolean }) {
  const colour = BAND_VAR[pairing.band];
  return (
    <div className="cb-result">
      {/* Generated image slot. Three distinct states — a picture, a not-yet-spent
       * placeholder, and a visible failure. Collapsing the last two would hide a
       * real operational problem behind "coming soon". */}
      <div className="cb-image">
        {pairing.image?.status === "ready" && pairing.image.path ? (
          <img
            src={mediaUrl(pairing.image.path)}
            alt={`${pairing.species_a.name} × ${pairing.species_b.name} shown planted in this zone`}
            loading="lazy"
          />
        ) : pairing.image?.status === "failed" ? (
          <div className="cb-image-pending cb-image-failed">
            <span className="eyebrow">Generated pairing image</span>
            <p className="provenance">
              Generation failed for this pairing
              {pairing.image.detail ? `: ${pairing.image.detail}` : "."} Re-run{" "}
              <code>scripts/generate_pairing_images.py --retry-failed</code>.
            </p>
          </div>
        ) : (
          <div className="cb-image-pending">
            <span className="eyebrow">Generated pairing image</span>
            <p className="provenance">
              Not yet generated. Grounding photographs for both parents are already
              collected from GBIF.
            </p>
          </div>
        )}
      </div>

      {/* Said on the image itself, not only in the sources disclosure: this is a
        * composite, and the one thing a viewer must not conclude is that they are
        * looking at a photograph of a plant that exists. */}
      {pairing.image?.status === "ready" && (
        <p className="provenance cb-image-caption">
          AI-generated composite: this zone's real Street View photograph with the
          pairing inserted. Not a photograph of an existing plant.
          {/* The image is generated for every pairing, including the ones the model
            * rejects. Without this clause a photorealistic picture of a cross scored
            * 0.00 would quietly contradict the score sitting directly beneath it. */}
          {(pairing.band === "Not viable" || pairing.band === "Unlikely") &&
            " This cross scores as " +
              pairing.band.toLowerCase() +
              " — the image shows what was asked for, not something the model says could grow."}
        </p>
      )}

      <div className="cb-score-row">
        <div
          className={`score-dial score-reveal${revealed ? " is-revealed" : ""}`}
          style={
            {
              "--score-target": pairing.confidence,
              "--fill": colour,
            } as React.CSSProperties
          }
          role="img"
          aria-label={`Confidence ${(pairing.confidence * 100).toFixed(0)} percent, ${pairing.band}`}
        >
          <span className="cb-score-num tabular">
            {(pairing.confidence * 100).toFixed(0)}
          </span>
        </div>

        <div className="cb-score-meta">
          <span className="pill" style={{ background: colour, color: "#fff" }}>
            {pairing.band}
          </span>
          <p className="cb-score-label">Predicted compatibility</p>
          <p className="provenance">
            {(pairing.confidence_low * 100).toFixed(0)}–
            {(pairing.confidence_high * 100).toFixed(0)}% uncertainty range
          </p>
        </div>
      </div>

      {/* The interval bar renders the uncertainty, not just the point estimate. */}
      <div
        className={`score-interval score-reveal${revealed ? " is-revealed" : ""}`}
        style={
          {
            "--score-target": pairing.confidence,
            "--interval-low": pairing.confidence_low,
            "--interval-high": pairing.confidence_high,
            "--fill": colour,
          } as React.CSSProperties
        }
        aria-hidden="true"
      />

      <p className="cb-explanation">{pairing.explanation}</p>

      <dl className="cb-terms">
        <Term label="Crossability" value={pairing.terms.crossability} />
        <Term label="Hybrid evidence" value={pairing.terms.hybrid_evidence} />
        <Term label="Parental fit" value={pairing.terms.parental_merit} />
        <Term label="Complementarity" value={pairing.terms.complementarity} />
      </dl>

      {pairing.divergence_mya !== null && (
        <p className="provenance">
          Divergence {pairing.divergence_mya.toFixed(1)} Mya
          {pairing.divergence_ci &&
            ` (95% CI ${pairing.divergence_ci[0].toFixed(1)}–${pairing.divergence_ci[1].toFixed(1)})`}
          {pairing.divergence_studies > 0 &&
            ` across ${pairing.divergence_studies} studies`}
        </p>
      )}

      <details className="cb-sources">
        <summary className="provenance">
          Sources &amp; caveats ({pairing.citations.length + pairing.data_gaps.length})
        </summary>
        <ul>
          {pairing.citations.map((c) => (
            <li key={c} className="provenance">
              {c}
            </li>
          ))}
          {pairing.data_gaps.map((g) => (
            <li key={g} className="provenance cb-gap">
              {g}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function Term({ label, value }: { label: string; value: number }) {
  return (
    <div className="cb-term">
      <dt>{label}</dt>
      <dd>
        <span
          className="cb-term-bar"
          style={{ "--v": value } as React.CSSProperties}
          aria-hidden="true"
        />
        <span className="tabular faint">{value.toFixed(2)}</span>
      </dd>
    </div>
  );
}
