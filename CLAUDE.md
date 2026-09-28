# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Two independent pydcs scripts, plus a reusable module:

- `dcs_maps_to_kmz.py` exports every DCS World terrain known to pydcs as a KMZ for Google Earth (visual reference, not mission planning). Layers: each terrain's DCS Cartesian origin (0,0), its bounds rectangle, curated high-detail polygons, and pydcs airfields.
- `channel_drone_gunnery.py` generates `channel_drone_gunnery.miz`, a WWII gunnery-practice mission on The Channel: a player Spitfire LF Mk IX plus three passive German formations (1 Ju-88 leading 2 Bf-109s) flying racetracks at 5k/12k/20k ft. It also writes `_low`/`_medium`/`_high` variants with a single formation each (`VARIANTS`).
- `loss_tracker/` is a mission-generator-agnostic module. `add_loss_tracker(mission, LossTrackerConfig(...))` embeds `loss_tracker.lua`, which credits an enemy aircraft loss (kill, crash, ejection, landing away from an allied base) to the last player-coalition attacker, announces it with the rounds fired for it, and shows a full-screen summary (MISSION COMPLETE / OUT OF AMMO / MISSION FAILED). Every enemy lost or every human pilot out of ammo sets `end_flag` after `end_mission_delay_s`; an `EndMission` trigger watches it.

## Commands

A `.claude/settings.json` SessionStart hook creates `.venv` (from `requirements.txt`) if missing and activates it for every Bash call, so `python` is the venv interpreter (Python 3.9: keep `from __future__ import annotations`, no runtime 3.10+ syntax).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt  # manual setup; pydcs comes from the dcs-retribution fork (newer terrains)

python dcs_maps_to_kmz.py --list      # origin/bounds table per terrain, "(override)" marks corrected bounds
python dcs_maps_to_kmz.py             # writes dcs_world_maps.kmz
python dcs_maps_to_kmz.py --output out.kmz --detail-regions other.json --bounds-overrides other.json --no-airports --samples-per-edge 50

python channel_drone_gunnery.py      # writes channel_drone_gunnery{,_low,_medium,_high}.miz next to the script
python install_missions.py           # copies every .miz here into ~/Saved Games/DCS/Missions and the GUI hook into ../Scripts/Hooks (skips if missing; --dest, --no-hook)

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
- The player group and fighters use their aircraft type's `radio_frequency`; pydcs's 251 MHz group default is invalid for WWII radios and DCS rejects it for the player.
- Callsigns and board numbers are random per run, so diffs between generated `.miz` files show those even when nothing changed.

## Loss tracker notes (`loss_tracker/`)

- `loss_tracker.lua` runs in DCS's Lua 5.1 scripting environment: no `goto`, integer division or other 5.2+ features. Plain DCS API only, no MIST/MOOSE.
- Config reaches Lua as a `LossTrackerConfig = {...}` global prepended (`to_lua()`) to a per-mission temp copy of `loss_tracker.lua`, embedded with `DoScriptFile`. Don't use `DoScript` or text-bearing actions: in DCS, `getValueDictByKey` failed for a pydcs `DictKey_Translation_*` key and ran the key name as Lua. Keep Lua defaults in sync with the dataclass.
- Every enemy aircraft resolves exactly once. Non-kill events are queued for `resolve_delay_s` so a following `S_EVENT_KILL` can claim them as a shot-down; the first reason otherwise wins.
- Event objects may be dead, so every DCS method call goes through `try()` (pcall). Aircraft are identified by group category, not `Unit:getCategory()`, whose meaning changed in DCS 2.9.
- A 5 s poll catches aircraft that vanish or stop on the ground without an event. Aircraft that land at an allied base are marked `safe`, so a later despawn resolves as `returned`, which never earns credit.
- Rounds fired come from `Unit:getAmmo()` totals: the baseline is taken at the shooter's first `SHOOTING_START`/`SHOT` and read again at each credited loss (DCS has no per-round gun event). `getAmmo()` returns nil when empty, so treat nil on a live unit as 0. Stats are keyed by attacker label, so a respawned player's count carries over, and an ammo increase raises the baseline (rearm).
- The summary is `outTextForCoalition(..., clearview=true)` with a `==========  TITLE  ==========` first line. Mission scripts can't draw UI or write files, so `LossTrackerGameGUI.lua` (a GameGUI hook in `Saved Games/DCS/Scripts/Hooks`, installed by `install_missions.py`) receives it through `onTriggerMessage`, shows it by spawning DCS's `Scripts/UI/ImportantNoticeDialog.dlg`, and writes losses/summaries with `Sim.writeDebriefing` (the debriefing log; each call is one `comment` event, so summaries go one line per call) plus `Logs/LossTracker.log`. Keep the title line and the ` - credited to ` / ` - no credit` loss wording in sync with the hook's parsing.
- In single player the hook pauses the simulation (`Sim.setPause`) while the window is open and resumes on close, only if it did the pausing. The summary's `Mission: <name>` line (from `mission_name`, defaulting to the sortie text) names the mission in the history log, because `Sim.getMissionName()` returns `tempMission`.
- In single player the hook sets `LossTracker.hookPresent` via `a_do_script` so the summary text only flashes for 2 s behind the window. Never in multiplayer: the tracker runs on the server.
- Mission goals: the tracker sets `score_flag` to the number of credited losses; `add_score_goals()` adds one goal per count k, to OFFLINE (what single player uses; BLUE/RED goals were ignored in a single-player test) and to the player's side (multiplayer), with `c_flag_equals(score_flag, k)` and score round(100k/N). Equality, not >=, because DCS sums the scores of all true goals. N is counted from the mission's enemy plane/helicopter groups, so `add_loss_tracker()` must run after they're added.
- Don't try to extend the post-mission debrief. After a mission DCS returns to the main menu and shows the debrief with `mmwWeb.show('debriefing')` (web UI, `web_MainMenu`, not shipped as editable files). `Scripts/UI/debriefing.lua` / `sim_debrief.dlg` is a different, in-simulation screen; a hook panel built against it never appeared and was removed. Only the mission result (goals) and the debriefing log reach the web debrief.
- The hook API is documented in the DCS install at `API/Sim_ControlAPI.md` (`Sim.*`, formerly `DCS.*`). `tests/gui_mock.lua` stubs it and the dxgui widgets for `tests/test_gui_hook.py`.
- `tests/test_loss_tracker.py` runs the Lua under `lupa.lua51` against `tests/dcs_mock.lua`. When the tracker calls a new DCS function, add it to the mock.
