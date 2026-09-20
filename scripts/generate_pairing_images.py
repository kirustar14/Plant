"""Generate the crossbreed pairing images — the one paid step in this project.

    .venv/bin/python scripts/generate_pairing_images.py --plan          # price it, spend nothing
    .venv/bin/python scripts/generate_pairing_images.py --quality low
    .venv/bin/python scripts/generate_pairing_images.py --limit 3       # smoke test
    .venv/bin/python scripts/generate_pairing_images.py --retry-failed  # only past failures
    .venv/bin/python scripts/generate_pairing_images.py --force         # regenerate everything

Each image composites the proposed pairing onto that zone's real Street View
photograph. Safe to interrupt and re-run: the store is written after every image, and
a pairing already generated is skipped unless --force.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.precompute import crossbreeds, pairing_images

logging.basicConfig(level=logging.WARNING, format="  %(levelname)-7s %(message)s")
BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"


def _print_estimate(est: dict) -> None:
    print(f"\n{BOLD}Batch estimate{RESET}\n{'='*74}")
    print(f"  images to generate                 {est['count']}")
    print(f"  quality / size                     {est['quality']} · {est['size']}")
    print(f"  price per image                    ${est['cost_per_image_usd']:.3f}")
    print(f"  {BOLD}estimated total cost               ${est['total_cost_usd']:.2f}{RESET}")
    print(f"  estimated wall-clock                ~{est['total_minutes']:.0f} min")
    print(f"\n{DIM}  {est['note']}{RESET}")


def _print_tiers(count: int) -> None:
    print(f"\n{BOLD}Cost by quality tier{RESET}\n{'='*74}")
    print(f"  {'quality':<12}{'per image':>12}{'total':>12}{'time':>12}")
    print(f"  {'-'*12}{'-'*12}{'-'*12}{'-'*12}")
    for q in ("low", "medium", "high"):
        e = pairing_images.estimate(count, q)
        print(f"  {q:<12}{'$'+format(e['cost_per_image_usd'],'.3f'):>12}"
              f"{'$'+format(e['total_cost_usd'],'.2f'):>12}"
              f"{format(e['total_minutes'],'.0f')+'m':>12}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true",
                    help="report cost and time, generate nothing")
    ap.add_argument("--quality", choices=("low", "medium", "high"), default="medium")
    ap.add_argument("--limit", type=int, default=None,
                    help="generate at most N images (smoke test)")
    ap.add_argument("--force", action="store_true",
                    help="regenerate every pairing, including ones already done")
    ap.add_argument("--retry-failed", action="store_true",
                    help="attempt only pairings previously marked failed")
    ap.add_argument("--yes", action="store_true",
                    help="skip the spend confirmation prompt")
    args = ap.parse_args()

    store = crossbreeds.load()
    if store is None:
        print(f"{RED}No precomputed store.{RESET} Run "
              f"scripts/precompute_crossbreeds.py first.")
        return 1

    counts = pairing_images.recount(store)
    print(f"\n{BOLD}Crossbreed pairing images{RESET}")
    print(f"{DIM}  composited onto each zone's real Street View photograph{RESET}")
    print(f"\n  store: {store['pairing_count']} pairings across "
          f"{store['zone_count']} zones")
    print(f"  ready {GREEN}{counts['ready']}{RESET} · "
          f"failed {RED}{counts['failed']}{RESET} · "
          f"pending {YELLOW}{counts['pending']}{RESET}")

    outstanding = pairing_images.pending_pairings(store, force=args.force)
    if args.retry_failed:
        outstanding = [
            (z, p) for z, p in outstanding
            if (p.get("image") or {}).get("status") == "failed"
        ]
    if args.limit is not None:
        outstanding = outstanding[: args.limit]

    if not outstanding:
        print(f"\n{GREEN}Nothing to generate.{RESET} "
              f"Use --force to regenerate, --retry-failed to retry failures.\n")
        return 0

    est = pairing_images.estimate(len(outstanding), args.quality)
    _print_estimate(est)
    if args.plan:
        _print_tiers(len(outstanding))
        print(f"\n{DIM}  --plan: nothing generated, nothing spent.{RESET}\n")
        return 0

    # Real money. Never implicit.
    if not args.yes:
        print()
        reply = input(
            f"  Spend approximately ${est['total_cost_usd']:.2f} on "
            f"{est['count']} images? [y/N] "
        ).strip().lower()
        if reply not in ("y", "yes"):
            print(f"\n{DIM}  Cancelled. Nothing generated, nothing spent.{RESET}\n")
            return 0

    references = store.get("reference_images", {})
    print(f"\n{BOLD}Generating{RESET}\n{'='*74}")

    ready = failed = 0
    for i, (zone, pairing) in enumerate(outstanding, 1):
        label = (f"{pairing['species_a']['name']} x {pairing['species_b']['name']}")
        print(f"  {i:>3}/{len(outstanding)}  {zone['zone_id'][:18]:<20}"
              f"{label[:38]:<40}", end="", flush=True)

        result = pairing_images.generate_one(
            pairing,
            zone_id=zone["zone_id"],
            zone_name=zone["name"],
            placement=zone["placement"],
            references=references,
            quality=args.quality,
        )
        pairing_images.apply_result(store, result)

        if result.status == "ready":
            ready += 1
            print(f"{GREEN}ok{RESET} {result.seconds:.0f}s")
        else:
            failed += 1
            print(f"{RED}FAILED{RESET} {result.detail}")

        # Written after every image so an interrupted batch keeps its progress and a
        # re-run resumes rather than re-billing what already succeeded.
        pairing_images.recount(store)
        crossbreeds.save(store)

    counts = pairing_images.recount(store)
    crossbreeds.save(store)

    print(f"\n{BOLD}Done{RESET}\n{'='*74}")
    print(f"  generated {GREEN}{ready}{RESET}, failed {RED}{failed}{RESET} this run")
    print(f"  store now: ready {GREEN}{counts['ready']}{RESET} · "
          f"failed {RED}{counts['failed']}{RESET} · "
          f"pending {YELLOW}{counts['pending']}{RESET}")
    print(f"  actual spend ≈ ${ready * est['cost_per_image_usd']:.2f} "
          f"(plus input image tokens)")

    if counts["failed"]:
        print(f"\n{DIM}  failures are recorded per pairing with a reason; "
              f"re-run with --retry-failed{RESET}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
