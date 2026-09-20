"""Fetch the Street View "before" photograph for every zone.

    .venv/bin/python scripts/fetch_streetview.py [--force]

Metadata is checked first for each zone and is free, so a zone without coverage
never triggers a billable image request. Images are cached on disk; re-running skips
zones already fetched unless --force is passed.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.sources import streetview
from backend.app.zones import ZONES

logging.basicConfig(level=logging.WARNING, format="  %(levelname)-7s %(message)s")
BOLD, DIM, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[0m"


def main() -> int:
    force = "--force" in sys.argv

    print(f"\n{BOLD}Street View 'before' photographs{RESET}")
    print(f"{DIM}  metadata checked first (free); images billed only where coverage "
          f"exists{RESET}\n")

    print(f"  {'Zone':<32}{'Status':<10}{'Captured':<10}{'Dist':>7}{'Head':>7}")
    print(f"  {'-'*32}{'-'*10}{'-'*10}{'-'*7}{'-'*7}")

    ok = 0
    denied = 0
    for zone in ZONES:
        photo = streetview.fetch_photo(
            zone.zone_id, zone.latitude, zone.longitude, force=force
        )
        if photo.available:
            ok += 1
            print(f"  {zone.name[:31]:<32}{GREEN}OK{RESET:<8}"
                  f"{str(photo.captured or '-'):<10}"
                  f"{(f'{photo.pano_distance_m:.0f}m' if photo.pano_distance_m else '-'):>7}"
                  f"{(f'{photo.heading:.0f}°' if photo.heading is not None else '-'):>7}")
        else:
            if photo.status == "REQUEST_DENIED":
                denied += 1
            print(f"  {zone.name[:31]:<32}{RED}{photo.status[:9]:<9}{RESET}")

    print(f"\n  {ok}/{len(ZONES)} zones have a photograph")

    if denied:
        print(f"\n{BOLD}Street View Static API is not enabled{RESET}")
        print("  Enable it, then re-run this script:\n")
        print("    gcloud services enable street-view-image-backend.googleapis.com \\")
        print("      --project <your-project-id>\n")
        print(f"{DIM}  Also check the API key has no restriction excluding Street "
              f"View:{RESET}")
        print(f"{DIM}  Console → APIs & Services → Credentials → key → API "
              f"restrictions{RESET}\n")
        return 1

    if ok:
        print(f"{DIM}  Images cached in {streetview.IMAGE_DIR}{RESET}")
        print(f"{DIM}  Served unmodified at /media/streetview/ so the Google "
              f"attribution stays intact{RESET}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
