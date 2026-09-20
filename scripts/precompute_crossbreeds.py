"""Build the precomputed crossbreed store.

    .venv/bin/python scripts/precompute_crossbreeds.py

Scores only — image generation is a separate paid step. Safe to re-run; it
overwrites atomically and keeps no partial state.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.precompute import crossbreeds

logging.basicConfig(level=logging.INFO, format="  %(levelname)-7s %(message)s")
BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def main() -> int:
    print(f"\n{BOLD}Precomputing crossbreed pairings{RESET}")
    print(f"{DIM}  scores + GBIF reference imagery; no paid generation{RESET}\n")

    store = crossbreeds.build()
    path = crossbreeds.save(store)

    print(f"\n{BOLD}Written{RESET}\n{'='*74}")
    print(f"  {path}")
    print(f"  {path.stat().st_size / 1024:.0f} KB · built in {store['build_seconds']}s")
    print(f"  {store['zone_count']} zones · {store['pairing_count']} pairings")

    print(f"\n{BOLD}Per zone{RESET}\n{'='*74}")
    print(f"  {'Zone':<32}{'Placement':<14}{'Ambitious':>10}{'Safer':>7}{'Best':>7}")
    print(f"  {'-'*32}{'-'*14}{'-'*10}{'-'*7}{'-'*7}")
    for z in store["zones"].values():
        best = max(
            (p["confidence"] for p in z["ambitious"] + z["safer"]), default=0.0
        )
        print(f"  {z['name'][:31]:<32}{z['placement']:<14}"
              f"{len(z['ambitious']):>10}{len(z['safer']):>7}{best:>7.3f}")

    print(f"\n{BOLD}Band distribution across all pairings{RESET}\n{'='*74}")
    bands: dict[str, int] = {}
    for z in store["zones"].values():
        for p in z["ambitious"] + z["safer"]:
            bands[p["band"]] = bands.get(p["band"], 0) + 1
    total = sum(bands.values())
    for band in ("Promising", "Plausible", "Unlikely", "Not viable"):
        n = bands.get(band, 0)
        bar = "█" * round(n / total * 40) if total else ""
        print(f"  {band:<12}{n:>4}  {bar}")

    print(f"\n{BOLD}GBIF reference imagery (grounds later generation){RESET}\n{'='*74}")
    refs = store["reference_images"]
    with_imagery = [k for k, v in refs.items() if v]
    without = [k for k, v in refs.items() if not v]
    print(f"  {len(with_imagery)}/{len(refs)} species have open-licence photographs")
    if without:
        print(f"\n{DIM}  no open-licence imagery — generation for these would be "
              f"ungrounded:{RESET}")
        for sid in without:
            print(f"    · {sid}")

    grounded_on_parent = sorted(
        {img["grounded_on"] for v in refs.values() for img in v if img["is_parent_species"]}
    )
    if grounded_on_parent:
        print(f"\n{DIM}  cultivars grounded on parent species (no GBIF record of their "
              f"own): {len(grounded_on_parent)} species{RESET}")

    print(f"\n{BOLD}Images{RESET}\n{'='*74}")
    pending = sum(
        1 for z in store["zones"].values()
        for p in z["ambitious"] + z["safer"]
        if p["image"]["status"] == "pending"
    )
    print(f"  {pending} pairings awaiting generation (paid step, run separately)")
    print(f"{DIM}    .venv/bin/python scripts/generate_pairing_images.py --plan"
          f"   # price it first{RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
