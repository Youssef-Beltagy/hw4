"""
Fetch the REST Countries v3.1 dataset and vendor a normalized snapshot to
data/countries.json.

We use a vendored snapshot (instead of calling the live API at runtime) so the
app has no network dependency, starts fast, and produces deterministic results
in tests. Re-run this script to refresh:

    python3 scripts/fetch_countries.py

Source: https://restcountries.com/v3.1/all
Filter: UN member states only (~193 records) to avoid disputed entries.
"""

from __future__ import annotations

import json
import statistics
import sys
import urllib.request
from pathlib import Path

# REST Countries v3.1 /all enforces a hard cap of 10 fields per request, so we
# split the fields across two batches and merge them by cca3.
API_BASE = "https://restcountries.com/v3.1/all"
FIELD_BATCHES = [
    ["cca3", "name", "cca2", "capital", "region", "subregion", "languages", "borders", "landlocked", "unMember"],
    ["cca3", "area", "population", "latlng", "flag", "altSpellings"],
]

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "data" / "countries.json"

# Upstream data corrections. REST Countries v3.1 has a small number of records
# where the `unMember` flag disagrees with the UN's published membership list.
# We patch these explicitly rather than silently dropping countries.
#   - GNB (Guinea-Bissau): UN member since 1974, but marked unMember=false upstream.
UN_MEMBER_OVERRIDES: dict[str, bool] = {
    "GNB": True,
}


def _fetch_batch(fields: list[str]) -> list[dict]:
    url = f"{API_BASE}?fields={','.join(fields)}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "country-20q-dataset-builder/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_raw() -> list[dict]:
    """
    Download and merge country records across multiple field-batched requests.

    The REST Countries /all endpoint caps requests at 10 fields, so we make
    one request per batch and merge the results keyed by cca3. Every batch
    includes cca3 as the join key.
    """
    merged: dict[str, dict] = {}
    for fields in FIELD_BATCHES:
        batch = _fetch_batch(fields)
        for record in batch:
            key = record.get("cca3")
            if not key:
                continue
            merged.setdefault(key, {}).update(record)
    return list(merged.values())


def hemisphere_ns(lat: float) -> str:
    return "N" if lat >= 0 else "S"


def hemisphere_ew(lng: float) -> str:
    return "E" if lng >= 0 else "W"


def bucketize(values: list[float], value: float, labels: list[str]) -> str:
    """
    Assign `value` to a bucket based on its quantile position within `values`.

    `labels` defines the buckets in ascending order. The number of buckets
    equals len(labels). This gives us dataset-relative labels (so "large"
    means "large relative to other countries in the dataset") instead of
    arbitrary absolute thresholds.
    """
    if not values or value is None:
        return labels[0]
    quantiles = statistics.quantiles(values, n=len(labels), method="inclusive")
    for label, threshold in zip(labels[:-1], quantiles):
        if value <= threshold:
            return label
    return labels[-1]


def build_aliases(name_common: str, name_official: str, alt_spellings: list[str]) -> list[str]:
    """Build a deduplicated alias list preserving insertion order."""
    seen: set[str] = set()
    aliases: list[str] = []
    for candidate in [name_common, name_official, *alt_spellings]:
        if not candidate:
            continue
        # Skip bare country codes like "JP" that aren't useful as guess aliases.
        if len(candidate) <= 2:
            continue
        key = candidate.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        aliases.append(candidate.strip())
    return aliases


def normalize(raw: list[dict]) -> list[dict]:
    """Filter to UN members, derive convenience fields, and shape the output."""
    # Apply manual overrides for known upstream data errors before filtering.
    for c in raw:
        code = c.get("cca3")
        if code in UN_MEMBER_OVERRIDES:
            c["unMember"] = UN_MEMBER_OVERRIDES[code]

    un_members = [c for c in raw if c.get("unMember") is True]

    # Precompute distributions for bucketing. Filter out None/0 for area since a
    # handful of tiny states report area as 0 in the source data.
    areas = [c["area"] for c in un_members if c.get("area")]
    populations = [c["population"] for c in un_members if c.get("population")]

    area_labels = ["small", "medium", "large", "very_large"]
    pop_labels = ["tiny", "small", "medium", "large", "huge"]

    normalized: list[dict] = []
    for c in un_members:
        name = c.get("name", {}) or {}
        name_common = name.get("common", "")
        name_official = name.get("official", "")

        capitals = c.get("capital") or []
        capital = capitals[0] if capitals else None

        latlng = c.get("latlng") or [0.0, 0.0]
        lat = float(latlng[0]) if len(latlng) >= 1 else 0.0
        lng = float(latlng[1]) if len(latlng) >= 2 else 0.0

        borders = list(c.get("borders") or [])
        languages = sorted((c.get("languages") or {}).values())

        area = c.get("area") or 0.0
        population = c.get("population") or 0

        record = {
            "name": name_common,
            "official_name": name_official,
            "aliases": build_aliases(
                name_common, name_official, c.get("altSpellings") or []
            ),
            "cca2": c.get("cca2", ""),
            "cca3": c.get("cca3", ""),
            "capital": capital,
            "region": c.get("region") or None,
            "subregion": c.get("subregion") or None,
            "languages": languages,
            "borders": borders,
            "landlocked": bool(c.get("landlocked", False)),
            "is_island": len(borders) == 0 and not c.get("landlocked", False),
            "area_km2": area,
            "population": population,
            "latlng": [lat, lng],
            "hemisphere_ns": hemisphere_ns(lat),
            "hemisphere_ew": hemisphere_ew(lng),
            "area_bucket": bucketize(areas, area, area_labels) if area else "small",
            "population_bucket": bucketize(populations, population, pop_labels) if population else "tiny",
            "flag": c.get("flag", ""),
        }
        normalized.append(record)

    # Stable, deterministic order for clean diffs.
    normalized.sort(key=lambda r: r["cca3"])
    return normalized


def main() -> int:
    print(f"Fetching from {API_BASE} in {len(FIELD_BATCHES)} field batch(es)")
    raw = fetch_raw()
    print(f"  received {len(raw)} merged raw records")

    normalized = normalize(raw)
    print(f"  kept {len(normalized)} UN member states after filtering")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "https://restcountries.com/v3.1/all",
        "filter": "unMember == true",
        "count": len(normalized),
        "schema_version": 1,
        "countries": normalized,
    }
    OUTPUT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(f"  wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
