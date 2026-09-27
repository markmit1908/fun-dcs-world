# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Two independent pydcs scripts:

- `dcs_maps_to_kmz.py` exports every DCS World terrain known to pydcs as a KMZ for Google Earth (visual reference, not mission planning). Layers: each terrain's DCS Cartesian origin (0,0), its bounds rectangle, curated high-detail polygons, and pydcs airfields.
- `channel_drone_gunnery.py` generates `channel_drone_gunnery.miz`, a WWII gunnery-practice mission on The Channel: a player Spitfire LF Mk IX plus three passive German formations (1 Ju-88 leading 2 Bf-109s) flying racetracks at 5k/12k/20k ft. It also writes `_low`/`_medium`/`_high` variants with a single formation each (`VARIANTS`).

## Commands

A `.claude/settings.json` SessionStart hook creates `.venv` (from `requirements.txt`) if missing and activates it for every Bash call, so `python` is the venv interpreter (Python 3.9: keep `from __future__ import annotations`, no runtime 3.10+ syntax).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt  # manual setup; pydcs comes from the dcs-retribution fork (newer terrains)

python dcs_maps_to_kmz.py --list      # origin/bounds table per terrain, "(override)" marks corrected bounds
python dcs_maps_to_kmz.py             # writes dcs_world_maps.kmz
python dcs_maps_to_kmz.py --output out.kmz --detail-regions other.json --bounds-overrides other.json --no-airports --samples-per-edge 50

python channel_drone_gunnery.py      # writes channel_drone_gunnery{,_low,_medium,_high}.miz next to the script

python -m pytest -q tests                                        # all tests
python -m pytest -q tests/test_kmz.py::test_detail_regions_inside_terrain
```

## Data files

- `bounds_overrides.json`: replacements for broken pydcs `Terrain.bounds`, in DCS metres (`x_min/x_max/y_min/y_max` + `source`). Currently Falklands (pydcs copies TheChannel's bounds) and MarianaIslands (10,000 km typo). Both are estimates from the airfield extent plus ~50 km. Used by default if present.
- `detail_regions.json`: approximate hand-drawn focus rectangles in `[lat, lon]`, keyed by terrain name. Not authoritative. Used by default if present.
- `dcs_detail_regions_example.json`: minimal format example.
- In both JSON loaders, top-level keys starting with `_` are ignored (`load_json`).

## Architecture notes

- **Terrain discovery is dynamic**: `discover_terrain_classes()` reflects over `dcs.terrain`, so which maps appear depends on the installed pydcs. Per-terrain failures are logged to stderr and skipped.
- **Projection**: DCS x/y → WGS84 always goes through `dcs.mapping.Point(x, y, terrain).latlng()` (terrain-specific pyproj transverse mercator). Never approximate km→degrees. For the reverse, use `Point.from_latlng`.
- **Axes**: DCS `x` is north-south, `y` is east-west. pydcs `Rectangle` is `x: bottom..top, y: left..right`, but several terrains store `top < bottom`, so all bounds go through `pydcs_bounds()` / `resolve_bounds()` and become a normalised `(x_min, x_max, y_min, y_max)` tuple. Rectangles are edge-sampled in Cartesian space before projecting.
- `bounds_warnings()` flags duplicate, >5,000 km, or reversed pydcs bounds that aren't overridden. Seeing a new warning other than "reversed" usually means a new pydcs terrain needs an entry in `bounds_overrides.json`.
- Detail-region keys are matched to terrains by `resolve_terrain_name()` (case-, underscore- and `Map`-suffix-insensitive, so `Sinai` → `SinaiMap`).
- **Coordinate order**: internal tuples and the JSON are `(lat, lon)`, and KML output is `lon,lat,alt`. The swap happens only in `add_point` / `add_polygon`. KML colors are `AABBGGRR`.
- **Tests** also check the data: override bounds must contain all of that terrain's airfields, and every detail-region vertex must fall inside its terrain's resolved bounds. Edit the JSON until they pass rather than loosening the tests.

## Mission script notes (`channel_drone_gunnery.py`)

- Aircraft are resolved by **exact** DCS type id (`find_plane`, called inside `main()`). No fuzzy matching, because a substring match could pick a different variant.
- pydcs defaults Germany to blue, so the script moves it to red.
- Targets are passive: Weapon Hold and No Reaction options go on waypoint 0 *before* the Orbit/Follow task. They also get `gun = 0` and empty pylons. The bomber flies a `Race-Track` orbit from its spawn point to the `end` waypoint, and the fighters `Follow` the bomber's group id.
- Offsets are computed geodesically with `pyproj.Geod`, then converted with `Point.from_latlng`.
- `quiet_dcs_install_lookup()` hides pydcs's DCS-install probing noise off Windows. That noise is a `pydcs` logger error plus a bare stderr `print`, once per group.
- Callsigns and board numbers are random per run, so diffs between generated `.miz` files show those even when nothing changed.
