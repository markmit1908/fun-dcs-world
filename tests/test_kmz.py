import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from dcs import mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dcs_maps_to_kmz as m  # noqa: E402


@pytest.fixture(scope="module")
def terrains():
    return [cls() for cls in m.discover_terrain_classes()]


@pytest.fixture(scope="module")
def overrides():
    return m.load_json(m.DEFAULT_OVERRIDES)


@pytest.fixture(scope="module")
def details():
    return m.load_json(m.DEFAULT_DETAIL_REGIONS)


def inside(bounds, x, y):
    x_min, x_max, y_min, y_max = bounds
    return x_min <= x <= x_max and y_min <= y <= y_max


def test_kml_structure(terrains, overrides, details):
    root = ET.fromstring(m.build_kml(terrains, 4, details, overrides))
    folders = {
        f.find(m.q("name")).text: f
        for f in root.find(m.q("Document")).findall(m.q("Folder"))
    }
    assert set(folders) == {
        "1 - DCS Cartesian Origins",
        "2 - Terrain Bounds (pydcs + overrides)",
        "3 - High-detail / Focus Regions",
        "4 - Airfields",
    }
    assert len(folders["1 - DCS Cartesian Origins"].findall(m.q("Placemark"))) == len(terrains)
    assert len(folders["4 - Airfields"].findall(m.q("Folder"))) == len(terrains)

    for coords in root.iter(m.q("coordinates")):
        for triple in coords.text.split():
            lon, lat, _ = map(float, triple.split(","))
            assert -90 <= lat <= 90 and -180 <= lon <= 180


def test_no_airports_flag(terrains):
    root = ET.fromstring(m.build_kml(terrains, 2, {}, {}, airports=False))
    names = [f.find(m.q("name")).text for f in root.iter(m.q("Folder"))]
    assert "4 - Airfields" not in names


def test_no_unhandled_bounds_warnings(terrains, overrides):
    bad = [w for w in m.bounds_warnings(terrains, overrides) if "reversed" not in w]
    assert bad == []


def test_airports_within_resolved_bounds(terrains, overrides):
    for t in terrains:
        if t.name not in overrides:
            continue  # raw pydcs bounds are known to be loose; only check our estimates
        bounds, _ = m.resolve_bounds(t, overrides)
        for a in t.airports.values():
            assert inside(bounds, a.position.x, a.position.y), (t.name, a.name)


def test_detail_regions_inside_terrain(terrains, overrides, details):
    by_name = {t.name: t for t in terrains}
    for key, regions in details.items():
        name = m.resolve_terrain_name(key, terrains)
        assert name is not None, key
        t = by_name[name]
        bounds, _ = m.resolve_bounds(t, overrides)
        for region in regions:
            for lat, lon in region["coordinates"]:
                p = mapping.Point.from_latlng(mapping.LatLng(lat, lon), t)
                assert inside(bounds, p.x, p.y), (name, region["name"], lat, lon)


def test_loader_skips_comment_and_resolves_alias(terrains):
    example = m.load_json(m.HERE / "dcs_detail_regions_example.json")
    assert "_comment" not in example
    assert m.resolve_terrain_name("Sinai", terrains) == "SinaiMap"
    assert m.resolve_terrain_name("Atlantis", terrains) is None
