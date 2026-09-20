"""Regenerate the DUSIB JJ-basti coordinate list used by the equity signal.

SOURCE
    Delhi Urban Shelter Improvement Board (DUSIB), "List of 675 J.J. Bastis with
    Latitude and Longitude", published 16-09-2022.
    https://delhishelterboard.in/main/wp-content/uploads/2022/09/JJC_List_675_Geo_Coordinates.pdf

WHY A LOCAL COPY
    The equity signal is a spatial join against these points, so the join has to be
    reproducible from the published record rather than from numbers typed in by hand.
    This script downloads the PDF, extracts the table, and writes the parsed points to
    backend/data/static/dusib_jj_bastis.json with provenance attached.

WHAT THIS RECORD IS NOT
    JJ bastis are one settlement type. Delhi also has unauthorised colonies,
    resettlement colonies and urban villages, none of which appear here. Absence of a
    basti is therefore not evidence that an area is affluent, and the UI says so.

Usage:  .venv/bin/python scripts/fetch_jj_bastis.py
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
import zlib
from pathlib import Path

SOURCE_URL = (
    "https://delhishelterboard.in/main/wp-content/uploads/2022/09/"
    "JJC_List_675_Geo_Coordinates.pdf"
)
SOURCE_LABEL = (
    "DUSIB — List of 675 J.J. Bastis with Latitude and Longitude (published 16-09-2022)"
)
OUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "backend" / "data" / "static" / "dusib_jj_bastis.json"
)

# Delhi bounding box, used to reject mis-parsed coordinate pairs.
LAT_RANGE = (28.0, 29.2)
LON_RANGE = (76.5, 77.8)


def _pdf_strings(pdf: bytes) -> list[str]:
    """Inflate the content streams and return their text-showing string operands.

    A deliberately small extractor: this PDF is a generated table with simple
    WinAnsi-encoded text, so decompressing the streams and pulling `( ... )` operands
    recovers the table without pulling in a PDF library as a runtime dependency.
    """
    out: list[str] = []
    literal = re.compile(rb"\((?:\\.|[^\\()])*\)", re.S)
    escapes = {
        b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b",
        b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\",
    }

    for m in re.finditer(rb"stream\r?\n", pdf):
        start = m.end()
        end = pdf.find(b"endstream", start)
        if end == -1:
            continue
        try:
            chunk = zlib.decompress(pdf[start:end])
        except zlib.error:
            continue  # image/ICC streams and other non-text objects
        for s in literal.finditer(chunk):
            body = s.group(0)[1:-1]
            body = re.sub(rb"\\([nrtbf()\\])", lambda e: escapes[e.group(1)], body)
            text = body.decode("latin-1", "replace")
            if text.strip():
                out.append(text)
    return out


def _tidy(name: str) -> str:
    """Rebuild word spacing the PDF's per-glyph positioning destroyed.

    The extractor yields e.g. 'M an g o lp u ri'. Every space inside a field is a
    positioning artefact, so they all come out; real word boundaries are then restored
    at capital letters and digits, which is where this document puts them.
    """
    s = name.replace(" ", "")
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
    # Split a run of capitals from the word it runs into: JJClusterat -> JJ Clusterat.
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    s = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", s)
    s = re.sub(r"\s*([,&])\s*", r"\1 ", s)
    return re.sub(r"\s+", " ", s).strip()


def _numeric(field: str) -> str | None:
    compact = field.replace(" ", "")
    return compact if re.fullmatch(r"-?\d+(\.\d+)?", compact) else None


def parse(pdf: bytes) -> list[dict]:
    # Fields arrive separated by the font-change marker the document emits between
    # table cells; squeeze each one to single spaces before interpreting it.
    fields = [
        re.sub(r"\s+", " ", f).strip()
        for f in " ".join(_pdf_strings(pdf)).split("x-none")
    ]
    fields = [f for f in fields if f]

    rows: list[dict] = []
    seen: set[int] = set()
    i = 0
    while i < len(fields) - 3:
        sno = _numeric(fields[i])
        if sno and "." not in sno:
            lat, lon = _numeric(fields[i + 2]), _numeric(fields[i + 3])
            if (
                lat and lon
                and LAT_RANGE[0] < float(lat) < LAT_RANGE[1]
                and LON_RANGE[0] < float(lon) < LON_RANGE[1]
                and int(sno) not in seen
            ):
                seen.add(int(sno))
                rows.append({
                    "sno": int(sno),
                    "name": _tidy(fields[i + 1]),
                    "latitude": round(float(lat), 6),
                    "longitude": round(float(lon), 6),
                })
                i += 4
                continue
        i += 1

    rows.sort(key=lambda r: r["sno"])
    return rows


def main() -> int:
    print(f"Fetching {SOURCE_URL}")
    with urllib.request.urlopen(SOURCE_URL, timeout=120) as resp:
        pdf = resp.read()
    print(f"  {len(pdf):,} bytes")

    rows = parse(pdf)
    if len(rows) < 600:
        print(
            f"ERROR: parsed only {len(rows)} rows — the document layout has probably "
            f"changed. Refusing to overwrite the existing file with a partial list.",
            file=sys.stderr,
        )
        return 1

    expected = set(range(1, 676))
    missed = sorted(expected - {r["sno"] for r in rows})

    payload = {
        "source": SOURCE_LABEL,
        "source_url": SOURCE_URL,
        "published": "2022-09-16",
        "cluster_count": len(rows),
        # Recorded rather than silently dropped: the published list has 675 entries,
        # and any the parser could not recover are named so the gap is inspectable.
        "unparsed_serials": missed,
        "resolution_note": (
            "Each basti is published as a single representative point, not a boundary. "
            "A cluster whose point falls outside a zone may still extend into it, so "
            "in-zone counts are a lower bound."
        ),
        "coverage_note": (
            "JJ bastis only. Unauthorised colonies, resettlement colonies and urban "
            "villages are separate settlement types not recorded here, so the absence "
            "of a basti is not evidence that an area is affluent."
        ),
        "clusters": rows,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    print(f"Wrote {len(rows)} clusters to {OUT_PATH}")
    if missed:
        print(f"  could not parse serials: {missed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
