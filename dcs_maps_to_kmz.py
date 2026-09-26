#!/usr/bin/env python3
"""
dcs_maps_to_kmz.py

Generate a KMZ showing DCS terrain:
  - DCS Cartesian origin (0,0)
  - pydcs terrain/base bounds transformed with the terrain's real projection
  - optional per-terrain bounds overrides (for broken pydcs bounds)
  - pydcs airfields
  - optional high-detail/focus polygons supplied in a JSON file

The base boundary is edge-sampled in DCS Cartesian coordinates and then
converted to WGS84 using pydcs's terrain-specific pyproj transformer.
This avoids the inaccurate "km -> degrees" approximation.

Requirements:
    pip install pydcs pyproj

Recommended pydcs fork for newer terrains:
    pip install git+https://github.com/dcs-retribution/pydcs.git@retribution

Usage:
    python dcs_maps_to_kmz.py
    python dcs_maps_to_kmz.py --output dcs_maps.kmz
    python dcs_maps_to_kmz.py --detail-regions detail_regions.json
    python dcs_maps_to_kmz.py --samples-per-edge 50
    python dcs_maps_to_kmz.py --bounds-overrides bounds_overrides.json --no-airports
"""

from __future__ import annotations

import argparse
import inspect
import json
import math
import sys
from pathlib import Path
from typing import Iterable, Tuple
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import dcs.mapping as mapping
import dcs.terrain as terrain_module
from dcs.terrain.terrain import Terrain


HERE = Path(__file__).resolve().parent
DEFAULT_OVERRIDES = HERE / "bounds_overrides.json"
DEFAULT_DETAIL_REGIONS = HERE / "detail_regions.json"

# Bounds spanning more than this on either axis are almost certainly wrong.
MAX_SANE_SPAN_M = 5_000_000

# (x_min, x_max, y_min, y_max) in DCS Cartesian metres.
Bounds = Tuple[float, float, float, float]

KML_NS = "http://www.opengis.net/kml/2.2"
ET.register_namespace("", KML_NS)


def q(tag: str) -> str:
    return f"{{{KML_NS}}}{tag}"


def discover_terrain_classes() -> list[type[Terrain]]:
    """
    Discover Terrain subclasses exported from dcs.terrain.

    Current dcs-retribution/pydcs exports classes such as:
      Caucasus, Falklands, MarianaIslands, Nevada, Normandy,
      PersianGulf, Sinai, Syria, TheChannel, Kola,
      Afghanistan, Iraq, GermanyColdWar.
    """
    classes: list[type[Terrain]] = []

    for _, obj in inspect.getmembers(terrain_module, inspect.isclass):
        if obj is Terrain:
            continue
        try:
            if issubclass(obj, Terrain):
                classes.append(obj)
        except TypeError:
            pass

    # Deterministic order.
    return sorted(set(classes), key=lambda cls: cls.__name__.lower())


def dcs_to_wgs84(terrain: Terrain, x: float, y: float) -> tuple[float, float]:
    """
    Convert DCS 2-D Cartesian x/y to WGS84 (lat, lon)
    using pydcs's map-specific projection.
    """
    ll = mapping.Point(x, y, terrain).latlng()
    return ll.lat, ll.lng


def sample_segment(
    a: tuple[float, float],
    b: tuple[float, float],
    samples: int,
) -> Iterable[tuple[float, float]]:
    """
    Yield samples along a segment, including A but excluding B.
    """
    ax, ay = a
    bx, by = b
    for i in range(samples):
        t = i / samples
        yield (ax + (bx - ax) * t, ay + (by - ay) * t)


def load_json(path: Path | None) -> dict:
    """Load a JSON object, dropping top-level keys that start with '_'."""
    if path is None:
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def pydcs_bounds(terrain: Terrain) -> Bounds:
    """
    Normalised pydcs Rectangle bounds.

    pydcs Rectangle is x: bottom..top, y: left..right, but several terrains
    store top < bottom, so sort each axis.
    """
    b = terrain.bounds
    return (
        min(b.bottom, b.top), max(b.bottom, b.top),
        min(b.left, b.right), max(b.left, b.right),
    )


def resolve_bounds(terrain: Terrain, overrides: dict) -> tuple[Bounds, str]:
    """Return (bounds, source), preferring an override keyed by Terrain.name."""
    o = overrides.get(terrain.name)
    if o is None:
        return pydcs_bounds(terrain), "pydcs Terrain.bounds"
    bounds = (
        float(o["x_min"]), float(o["x_max"]),
        float(o["y_min"]), float(o["y_max"]),
    )
    return bounds, "override: " + o.get("source", "bounds_overrides.json")


def bounds_warnings(terrains: list[Terrain], overrides: dict) -> list[str]:
    """Flag suspicious raw pydcs bounds that are not already overridden."""
    warnings: list[str] = []
    seen: dict[Bounds, str] = {}
    for terrain in terrains:
        if terrain.name in overrides:
            continue
        b = terrain.bounds
        raw = pydcs_bounds(terrain)
        x_min, x_max, y_min, y_max = raw
        if x_max - x_min > MAX_SANE_SPAN_M or y_max - y_min > MAX_SANE_SPAN_M:
            warnings.append(f"{terrain.name}: bounds span > {MAX_SANE_SPAN_M / 1000:.0f} km")
        if raw in seen:
            warnings.append(f"{terrain.name}: bounds identical to {seen[raw]}")
        seen.setdefault(raw, terrain.name)
        if b.top < b.bottom or b.right < b.left:
            warnings.append(f"{terrain.name}: pydcs bounds reversed (normalised)")
    unknown = set(overrides) - {t.name for t in terrains}
    for name in sorted(unknown):
        warnings.append(f"override for unknown terrain {name!r} ignored")
    return warnings


def terrain_boundary_dcs(
    bounds: Bounds,
    samples_per_edge: int = 40,
) -> list[tuple[float, float]]:
    """Build an edge-sampled polygon from normalised Cartesian bounds."""
    x_min, x_max, y_min, y_max = bounds

    # Clockwise around the DCS Cartesian rectangle.
    corners = [
        (x_min, y_min),
        (x_max, y_min),
        (x_max, y_max),
        (x_min, y_max),
    ]

    result: list[tuple[float, float]] = []
    for i in range(4):
        a = corners[i]
        c = corners[(i + 1) % 4]
        result.extend(sample_segment(a, c, samples_per_edge))

    result.append(result[0])
    return result


def terrain_boundary_wgs84(
    terrain: Terrain,
    bounds: Bounds,
    samples_per_edge: int = 40,
) -> list[tuple[float, float]]:
    return [
        dcs_to_wgs84(terrain, x, y)
        for x, y in terrain_boundary_dcs(bounds, samples_per_edge)
    ]


def add_text(parent: ET.Element, tag: str, text: str) -> ET.Element:
    e = ET.SubElement(parent, q(tag))
    e.text = text
    return e


def add_style(doc: ET.Element, style_id: str, line_color: str, fill_color: str) -> None:
    style = ET.SubElement(doc, q("Style"), {"id": style_id})

    line = ET.SubElement(style, q("LineStyle"))
    add_text(line, "color", line_color)
    add_text(line, "width", "2.5")

    poly = ET.SubElement(style, q("PolyStyle"))
    add_text(poly, "color", fill_color)


def add_point_style(doc: ET.Element, style_id: str, scale: str = "1.1") -> None:
    style = ET.SubElement(doc, q("Style"), {"id": style_id})
    icon = ET.SubElement(style, q("IconStyle"))
    add_text(icon, "scale", scale)


def add_point(
    folder: ET.Element,
    name: str,
    lat: float,
    lon: float,
    description: str,
    style_url: str = "#origin",
) -> None:
    pm = ET.SubElement(folder, q("Placemark"))
    add_text(pm, "name", name)
    add_text(pm, "description", description)
    add_text(pm, "styleUrl", style_url)
    point = ET.SubElement(pm, q("Point"))
    add_text(point, "coordinates", f"{lon:.8f},{lat:.8f},0")


def add_polygon(
    folder: ET.Element,
    name: str,
    latlon: list[tuple[float, float]],
    description: str,
    style_url: str,
) -> None:
    pm = ET.SubElement(folder, q("Placemark"))
    add_text(pm, "name", name)
    add_text(pm, "description", description)
    add_text(pm, "styleUrl", style_url)

    poly = ET.SubElement(pm, q("Polygon"))
    outer = ET.SubElement(poly, q("outerBoundaryIs"))
    ring = ET.SubElement(outer, q("LinearRing"))

    # KML is lon,lat,alt.
    coords = " ".join(
        f"{lon:.8f},{lat:.8f},0"
        for lat, lon in latlon
    )
    add_text(ring, "coordinates", coords)


def add_airports(folder: ET.Element, terrain: Terrain) -> int:
    count = 0
    for airport in sorted(terrain.airports.values(), key=lambda a: a.name):
        lat, lon = dcs_to_wgs84(terrain, airport.position.x, airport.position.y)
        runways = ", ".join(r.name for r in airport.runways) or "none"
        add_point(
            folder,
            airport.name,
            lat,
            lon,
            f"{terrain.name} airfield id {airport.id}. Runways: {runways}.",
            "#airport",
        )
        count += 1
    return count


def _norm(name: str) -> str:
    name = name.lower().replace(" ", "").replace("_", "")
    return name[:-3] if name.endswith("map") else name


def resolve_terrain_name(key: str, terrains: list[Terrain]) -> str | None:
    """Match a detail-regions key to Terrain.name or class name (e.g. Sinai -> SinaiMap)."""
    for terrain in terrains:
        if _norm(key) in {_norm(terrain.name), _norm(terrain.__class__.__name__)}:
            return terrain.name
    return None


def add_optional_detail_regions(
    folder: ET.Element,
    details: dict,
    terrains: list[Terrain],
) -> None:
    """
    JSON format:

    {
      "Syria": [
        {
          "name": "Example detail area",
          "status": "current",
          "coordinates": [
            [35.0, 36.0],
            [35.0, 37.0],
            [36.0, 37.0],
            [36.0, 36.0]
          ],
          "notes": "lat, lon pairs",
          "source": "where the boundary came from (optional)"
        }
      ]
    }

    Coordinates are [lat, lon]. Keys starting with '_' are dropped by
    load_json; unknown terrain names are exported with a warning.
    """
    for key, regions in details.items():
        terrain_name = resolve_terrain_name(key, terrains)
        if terrain_name is None:
            print(f"WARNING: detail regions key {key!r} matches no terrain", file=sys.stderr)
            terrain_name = key
        for region in regions:
            coords = [
                (float(lat), float(lon))
                for lat, lon in region["coordinates"]
            ]
            if coords[0] != coords[-1]:
                coords.append(coords[0])

            status = region.get("status", "current").lower()
            style = "#future" if status in {"future", "development"} else "#detail"

            add_polygon(
                folder,
                f"{terrain_name} - {region['name']}",
                coords,
                " ".join(
                    s for s in (region.get("notes", ""), region.get("source", "") and
                                f"Source: {region['source']}") if s
                ),
                style,
            )


def build_kml(
    terrains: list[Terrain],
    samples_per_edge: int,
    details: dict,
    overrides: dict | None = None,
    airports: bool = True,
) -> bytes:
    overrides = overrides or {}
    root = ET.Element(q("kml"))
    doc = ET.SubElement(root, q("Document"))

    add_text(doc, "name", "DCS World terrain origins and extents")
    add_text(
        doc,
        "description",
        (
            "Base extents are read directly from pydcs Terrain.bounds and "
            "converted to WGS84 using each terrain's actual projection. "
            "Broken pydcs bounds are replaced from bounds_overrides.json. "
            "High-detail/focus regions are approximate and come from the optional JSON file."
        ),
    )

    add_point_style(doc, "origin")
    add_point_style(doc, "airport", "0.7")

    # KML color format = AABBGGRR.
    add_style(doc, "base",   "ff808080", "20808080")
    add_style(doc, "detail", "ff00aa00", "3000aa00")
    add_style(doc, "future", "ff00aaff", "2000aaff")

    origins = ET.SubElement(doc, q("Folder"))
    add_text(origins, "name", "1 - DCS Cartesian Origins")

    extents = ET.SubElement(doc, q("Folder"))
    add_text(extents, "name", "2 - Terrain Bounds (pydcs + overrides)")

    detail_folder = ET.SubElement(doc, q("Folder"))
    add_text(detail_folder, "name", "3 - High-detail / Focus Regions")

    airport_root = None
    if airports:
        airport_root = ET.SubElement(doc, q("Folder"))
        add_text(airport_root, "name", "4 - Airfields")

    for terrain in terrains:
        try:
            origin_lat, origin_lon = dcs_to_wgs84(terrain, 0.0, 0.0)
            bounds, source = resolve_bounds(terrain, overrides)
            x_min, x_max, y_min, y_max = bounds

            add_point(
                origins,
                f"{terrain.name} - DCS (0,0)",
                origin_lat,
                origin_lon,
                (
                    f"DCS Cartesian origin transformed with the terrain's "
                    f"pydcs projection. Lat/Lon: "
                    f"{origin_lat:.8f}, {origin_lon:.8f}"
                ),
            )

            boundary = terrain_boundary_wgs84(
                terrain,
                bounds,
                samples_per_edge=samples_per_edge,
            )

            add_polygon(
                extents,
                f"{terrain.name} - bounds",
                boundary,
                (
                    f"Cartesian bounds (metres) from {source}: "
                    f"x={x_min:.0f}..{x_max:.0f}, "
                    f"y={y_min:.0f}..{y_max:.0f}. "
                    f"Boundary sampled at {samples_per_edge} points per edge "
                    "and transformed with the terrain-specific projection."
                ),
                "#base",
            )

            if airport_root is not None:
                sub = ET.SubElement(airport_root, q("Folder"))
                add_text(sub, "name", terrain.name)
                add_airports(sub, terrain)

        except Exception as exc:
            print(
                f"WARNING: could not export {terrain.__class__.__name__}: {exc}",
                file=sys.stderr,
            )

    add_optional_detail_regions(detail_folder, details, terrains)

    return ET.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
    )


def export_summary(terrains: list[Terrain], overrides: dict) -> None:
    print(
        f"{'Terrain':28} {'Origin lat':>12} {'Origin lon':>12} "
        f"{'X min':>11} {'X max':>11} {'Y min':>11} {'Y max':>11}"
    )
    print("-" * 104)

    for terrain in terrains:
        try:
            lat, lon = dcs_to_wgs84(terrain, 0, 0)
            bounds, source = resolve_bounds(terrain, overrides)
            flag = "  (override)" if source.startswith("override") else ""
            print(
                f"{terrain.name:28} "
                f"{lat:12.6f} {lon:12.6f} "
                + " ".join(f"{v:11.0f}" for v in bounds)
                + flag
            )
        except Exception as exc:
            print(f"{terrain.__class__.__name__:28} ERROR: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dcs_world_maps.kmz"),
        help="Output KMZ path",
    )
    parser.add_argument(
        "--detail-regions",
        type=Path,
        default=DEFAULT_DETAIL_REGIONS if DEFAULT_DETAIL_REGIONS.exists() else None,
        help="JSON containing high-detail/focus polygons (default: detail_regions.json if present)",
    )
    parser.add_argument(
        "--bounds-overrides",
        type=Path,
        default=DEFAULT_OVERRIDES if DEFAULT_OVERRIDES.exists() else None,
        help="JSON of per-terrain bounds replacements (default: bounds_overrides.json if present)",
    )
    parser.add_argument(
        "--no-airports",
        action="store_true",
        help="Omit the airfields layer",
    )
    parser.add_argument(
        "--samples-per-edge",
        type=int,
        default=40,
        help="Number of projection samples per Cartesian rectangle edge",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print discovered terrain origins/bounds and exit",
    )
    args = parser.parse_args()

    terrain_classes = discover_terrain_classes()
    if not terrain_classes:
        raise RuntimeError(
            "No pydcs terrain classes were discovered. "
            "Check that pydcs is installed."
        )

    terrains: list[Terrain] = []
    for cls in terrain_classes:
        try:
            terrains.append(cls())
        except Exception as exc:
            print(
                f"WARNING: failed to instantiate {cls.__name__}: {exc}",
                file=sys.stderr,
            )

    overrides = load_json(args.bounds_overrides)
    for warning in bounds_warnings(terrains, overrides):
        print(f"WARNING: {warning}", file=sys.stderr)

    if args.list:
        export_summary(terrains, overrides)
        return 0

    details = load_json(args.detail_regions)
    kml = build_kml(
        terrains,
        samples_per_edge=max(2, args.samples_per_edge),
        details=details,
        overrides=overrides,
        airports=not args.no_airports,
    )

    # Validate the XML we are about to package.
    ET.fromstring(kml)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, "w", ZIP_DEFLATED) as kmz:
        kmz.writestr("doc.kml", kml)

    # Re-open and validate the exact KML stored in the KMZ.
    with ZipFile(args.output, "r") as kmz:
        ET.fromstring(kmz.read("doc.kml"))

    print(f"Wrote: {args.output.resolve()}")
    print(f"Terrains exported: {len(terrains)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
