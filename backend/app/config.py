"""Central configuration. Secrets come from the environment only — never hardcoded.

Loads .env if present (gitignored); real process env always wins, so deploys can
inject keys without a file on disk.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

# override=False: a real environment variable beats the .env file.
load_dotenv(REPO_ROOT / ".env", override=False)

# --- Paths -------------------------------------------------------------------
DATA_DIR = REPO_ROOT / "backend" / "data"
CACHE_DIR = DATA_DIR / "cache"
STATIC_DIR = DATA_DIR / "static"
GENERATED_IMAGES_DIR = DATA_DIR / "generated_images"

for _d in (CACHE_DIR, STATIC_DIR, GENERATED_IMAGES_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Secrets -----------------------------------------------------------------
OPENAQ_API_KEY = os.environ.get("OPENAQ_API_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "").strip()

GEE_SERVICE_ACCOUNT_JSON = os.environ.get("GEE_SERVICE_ACCOUNT_JSON", "").strip()
GEE_SERVICE_ACCOUNT_EMAIL = os.environ.get("GEE_SERVICE_ACCOUNT_EMAIL", "").strip()
GEE_PROJECT_ID = os.environ.get("GEE_PROJECT_ID", "").strip()

# --- Shared HTTP identity ----------------------------------------------------
# Overpass returns 406 for the default client User-Agent. This is not optional.
USER_AGENT = (
    "Plan-Urban-Greening/1.0 (HackMIT 2026 research project; "
    "+https://github.com/kirustar14/Plant)"
)

# --- Delhi ------------------------------------------------------------------
DELHI_CENTER = (28.6139, 77.2090)
DELHI_BBOX = (28.40, 76.85, 28.75, 77.35)  # south, west, north, east

# --- OpenAQ station selection ------------------------------------------------
# OpenAQ returns 96 stations for the 25km Delhi radius and many are long dead — some
# last reported in 2018. Filtering is done on each station's `datetimeLast`, NOT on
# its name, for a concrete reason found during testing: OpenAQ carries duplicate
# station names under different IDs where one is live and the other is dead.
#   "Punjabi Bagh, Delhi - DPCC"  -> id 50   live, and id 5540 dead since 2018
#   "Anand Vihar, ... - DPCC"     -> id 235  live, and id 5509 dead since 2022
# Name matching would silently select a dead duplicate, so IDs and timestamps rule.
#
# These 7 are the brief's pull-tested core set, pinned by resolved ID. They anchor
# the headline city baseline. The freshness filter is what actually guarantees
# liveness, so the set self-heals if one of them goes quiet.
CORE_STATION_IDS: dict[int, str] = {
    17: "R K Puram, Delhi - DPCC",
    50: "Punjabi Bagh, Delhi - DPCC",
    235: "Anand Vihar, New Delhi - DPCC",
    5404: "Pusa, Delhi - IMD",
    5570: "Aya Nagar, New Delhi - IMD",
    5586: "Sirifort, Delhi - CPCB",
    5598: "Sector - 125, Noida, UP - UPPCB",
}

# Reference-grade networks: government regulatory monitors. Community low-cost
# sensors (typically PM-only, reporting pm1/um003) are measurably less accurate, so
# they are labelled and excluded from scoring rather than silently mixed in.
REFERENCE_NETWORKS = ("DPCC", "CPCB", "IMD", "UPPCB", "HSPCB", "IITM")

# A reading older than this is treated as absent rather than current.
MAX_READING_AGE_DAYS = 7

# Beyond this, a monitor is too far to characterise a 600m zone; the zone falls back
# to the city baseline and is labelled as doing so.
MAX_ZONE_STATION_DISTANCE_KM = 5.0


def missing_keys() -> list[str]:
    """Which required secrets are absent. Surfaced at startup and via /api/health."""
    missing = []
    if not OPENAQ_API_KEY:
        missing.append("OPENAQ_API_KEY")
    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")
    if not GOOGLE_API_KEY:
        missing.append("GOOGLE_API_KEY")
    return missing
