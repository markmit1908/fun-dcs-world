#!/usr/bin/env python3
"""
channel_drone_gunnery.py

Creates a DCS World .miz mission on The Channel terrain:

- Player: Spitfire LF Mk IX, airborne about 2 statute miles south of the
  western racetrack start.
- Targets: three altitude bands (low / medium / high).
- Each band has:
    * 1 German WWII bomber as racetrack leader
    * 2 German WWII fighters following the bomber in formation
- AI targets:
    * Weapon Hold
    * No Reaction to Threat
    * guns/ammunition removed where possible
- Scoring: loss_tracker counts a target as destroyed when it is shot down,
  crashes, ejects or lands off-field, and ends the mission once all are gone.
- Racetrack:
    Start: 50°50'6.86"N, 1°10'40.42"E
    End:   51°7'43.96"N, 1°35'53.10"E

Tested against the API layout of the dcs-retribution pydcs fork.

Install:
    python3 -m pip install git+https://github.com/dcs-retribution/pydcs.git@retribution

Run:
    python3 channel_drone_gunnery.py

Output (same player start in each; see VARIANTS):
    channel_drone_gunnery.miz         all three bands
    channel_drone_gunnery_low.miz     low band only
    channel_drone_gunnery_medium.miz  medium band only
    channel_drone_gunnery_high.miz    high band only
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import inspect
import logging
import sys

import dcs
import dcs.planes as planes
import dcs.task as task

from dcs.mapping import LatLng, Point, Vector2
from dcs.terrain import TheChannel
from dcs.unit import Skill

from loss_tracker import LossTrackerConfig, add_loss_tracker

try:
    from pyproj import Geod
except ImportError as exc:
    raise SystemExit(
        "pyproj is required. Install with: python3 -m pip install pyproj"
    ) from exc


OUTPUT_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Geographic setup
# ---------------------------------------------------------------------------

# User-provided coordinates, converted from DMS to decimal degrees.
START_LAT = 50 + 50 / 60 + 6.86 / 3600
START_LON = 1 + 10 / 60 + 40.42 / 3600

END_LAT = 51 + 7 / 60 + 43.96 / 3600
END_LON = 1 + 35 / 60 + 53.10 / 3600

PLAYER_OFFSET_M = 2.0 * 1609.344  # 2 statute miles due south

# Altitudes are MSL.
LOW_FT = 5_000
MED_FT = 12_000
HIGH_FT = 20_000

BANDS = {
    "LOW": LOW_FT,
    "MEDIUM": MED_FT,
    "HIGH": HIGH_FT,
}

# Output file name -> target bands in that mission. The player start is the
# same in every variant.
VARIANTS = {
    "channel_drone_gunnery.miz": ["LOW", "MEDIUM", "HIGH"],
    "channel_drone_gunnery_low.miz": ["LOW"],
    "channel_drone_gunnery_medium.miz": ["MEDIUM"],
    "channel_drone_gunnery_high.miz": ["HIGH"],
}

# Target speed. Kept moderate so a Spitfire can work the formations.
TARGET_SPEED_KMH = 360

# Player initial altitude/speed.
PLAYER_ALT_FT = 6_000
PLAYER_SPEED_KMH = 420

FT_TO_M = 0.3048


# ---------------------------------------------------------------------------
# Plane lookup
# ---------------------------------------------------------------------------

def all_plane_types():
    """Return all pydcs PlaneType subclasses exported by dcs.planes."""
    result = []
    for _, obj in inspect.getmembers(planes, inspect.isclass):
        try:
            if issubclass(obj, planes.PlaneType) and obj is not planes.PlaneType:
                result.append(obj)
        except TypeError:
            pass
    return result


def find_plane(*preferred_ids: str):
    """
    Resolve an aircraft by exact DCS type id, trying each id in order.

    No fuzzy matching: a substring match could silently pick a different
    variant (e.g. a clipped-wing Spitfire), so fail loudly instead.
    """
    types = all_plane_types()

    for wanted in preferred_ids:
        for cls in types:
            if getattr(cls, "id", "") == wanted:
                return cls

    available = sorted(
        getattr(cls, "id", cls.__name__)
        for cls in types
        if any(
            key in getattr(cls, "id", "").lower()
            for key in ("spit", "109", "190", "ju", "88")
        )
    )
    raise RuntimeError(
        f"Could not find any of {preferred_ids!r} in this pydcs installation.\n"
        f"Likely relevant installed plane IDs:\n  " + "\n  ".join(available)
    )


# DCS type ids, resolved in main() so importing this module has no side effects.
SPITFIRE_IDS = ("SpitfireLFMkIX",)
FIGHTER_IDS = ("Bf-109K-4",)
BOMBER_IDS = ("Ju-88A4",)


@contextmanager
def quiet_dcs_install_lookup():
    """
    Silence pydcs's DCS-install probing, which is noise off Windows.

    pydcs logs a registry error to the "pydcs" logger and print()s
    "Couldn't detect any installed DCS World version" once per group while
    scanning liveries. Drop exactly those; let all other stderr through.
    """
    logger = logging.getLogger("pydcs")
    old_level = logger.level
    old_stderr = sys.stderr

    class _Filter:
        def write(self, text):
            if "Couldn't detect any installed DCS World version" not in text:
                old_stderr.write(text)

        def __getattr__(self, name):
            return getattr(old_stderr, name)

    if sys.platform != "win32":
        logger.setLevel(logging.CRITICAL)
        sys.stderr = _Filter()
    try:
        yield
    finally:
        logger.setLevel(old_level)
        sys.stderr = old_stderr


# ---------------------------------------------------------------------------
# Mission helpers
# ---------------------------------------------------------------------------

def make_drone(group):
    """
    Make an AI aircraft group behave as a passive target.

    Put the options on the first route point so DCS applies them immediately.
    Also remove guns/pylons from the unit data where pydcs permits.
    """
    first = group.points[0]
    first.tasks.append(task.OptROE(task.OptROE.Values.WeaponHold))
    first.tasks.append(
        task.OptReactOnThreat(task.OptReactOnThreat.Values.NoReaction)
    )

    for unit in group.units:
        # gun is expressed as a percentage in pydcs payload data.
        unit.gun = 0
        unit.pylons = {}
        unit.chaff = 0
        unit.flare = 0
        unit.skill = Skill.Average


def configure_racetrack(group, start: Point, end: Point, altitude_m: int):
    """
    Give a group a racetrack orbit between start and end.
    """
    # The group's first point is already at start because it spawned inflight.
    first = group.points[0]

    # DCS OrbitAction uses the next route point to establish the second end
    # of a race-track orbit.
    first.tasks.append(
        task.OrbitAction(
            altitude=altitude_m,
            speed=TARGET_SPEED_KMH,
            pattern=task.OrbitAction.OrbitPattern.RaceTrack,
        )
    )

    group.add_waypoint(
        end,
        altitude=altitude_m,
        speed=TARGET_SPEED_KMH,
    )


def create_target_band(
    mission: dcs.Mission,
    germany,
    bomber_type,
    fighter_type,
    name: str,
    altitude_ft: int,
    start: Point,
    end: Point,
):
    """
    Create one bomber leader and a two-fighter formation following it.
    """
    altitude_m = int(altitude_ft * FT_TO_M)

    # Bomber leads the racetrack.
    bomber = mission.flight_group_inflight(
        germany,
        f"{name} Bomber",
        bomber_type,
        start,
        altitude=altitude_m,
        speed=TARGET_SPEED_KMH,
        maintask=task.Nothing,
        group_size=1,
    )
    make_drone(bomber)
    configure_racetrack(bomber, start, end, altitude_m)

    # Fighters begin slightly behind the bomber and follow it.
    heading = start.heading_between_point(end)
    fighter_start = start.point_from_heading((heading + 180) % 360, 250)

    fighters = mission.flight_group_inflight(
        germany,
        f"{name} Fighters",
        fighter_type,
        fighter_start,
        altitude=altitude_m,
        speed=TARGET_SPEED_KMH,
        maintask=task.Nothing,
        group_size=2,
    )
    make_drone(fighters)
    # Use the type's own radio frequency instead of the pydcs 251 MHz default
    # (see create_player).
    fighters.set_frequency(fighter_type.radio_frequency)

    # Replace any default tasking on the first waypoint only with passive
    # options + Follow. We preserve the WeaponHold/NoReaction tasks added above.
    fighters.points[0].tasks.append(
        task.Follow(
            groupid=bomber.id,
            group_offset=Vector2(-250, 0),
            altitude_difference=0,
        )
    )

    # Give the fighter group a downstream waypoint as a harmless route fallback.
    fighters.add_waypoint(
        end,
        altitude=altitude_m,
        speed=TARGET_SPEED_KMH,
    )

    return bomber, fighters


def create_player(
    mission: dcs.Mission,
    uk,
    player_type,
    player_point: Point,
    start: Point,
):
    altitude_m = int(PLAYER_ALT_FT * FT_TO_M)

    player = mission.flight_group_inflight(
        uk,
        "Player Spitfire",
        player_type,
        player_point,
        altitude=altitude_m,
        speed=PLAYER_SPEED_KMH,
        maintask=task.Nothing,
        group_size=1,
    )

    player.units[0].set_player()
    # pydcs defaults every group to 251 MHz, which the Spitfire's radio can't
    # tune, so DCS rejects the mission. Set this after set_player() so the
    # channel 1 preset matches too.
    player.set_frequency(player_type.radio_frequency)

    # First navigation point takes the player toward the target lane.
    player.add_waypoint(
        start,
        altitude=altitude_m,
        speed=PLAYER_SPEED_KMH,
    )

    return player


# ---------------------------------------------------------------------------
# Build mission
# ---------------------------------------------------------------------------

def main():
    spitfire = find_plane(*SPITFIRE_IDS)
    fighter = find_plane(*FIGHTER_IDS)
    bomber = find_plane(*BOMBER_IDS)

    for filename, band_names in VARIANTS.items():
        output = OUTPUT_DIR / filename
        with quiet_dcs_install_lookup():
            build_and_save(spitfire, fighter, bomber, band_names, output)
        bands = ", ".join(f"{name} {BANDS[name]:,} ft" for name in band_names)
        print(f"Wrote {output} ({bands})")

    print()
    print("Aircraft selected:")
    print(f"  Player : {spitfire.id}")
    print(f"  Fighter: {fighter.id}")
    print(f"  Bomber : {bomber.id}")
    print()
    print(f"Racetrack start: {START_LAT:.8f}, {START_LON:.8f}")
    print(f"Racetrack end  : {END_LAT:.8f}, {END_LON:.8f}")
    print("Player starts approximately 2 statute miles south of racetrack start.")


def build_and_save(spitfire, fighter, bomber, band_names, output: Path):
    terrain = TheChannel()
    mission = dcs.Mission(terrain)

    # Put the UK on blue and Germany on red.
    # pydcs defaults Germany to blue, so explicitly swap it.
    blue = mission.coalition["blue"]
    red = mission.coalition["red"]

    germany = blue.remove_country("Germany")
    red.add_country(germany)

    uk = blue.country("UK")
    if uk is None:
        raise RuntimeError("Could not find UK in the blue coalition.")

    start = Point.from_latlng(LatLng(START_LAT, START_LON), terrain)
    end = Point.from_latlng(LatLng(END_LAT, END_LON), terrain)

    # Calculate an actual geodesic point 2 statute miles due south of START,
    # then transform that geographic point into DCS map coordinates.
    geod = Geod(ellps="WGS84")
    player_lon, player_lat, _ = geod.fwd(
        START_LON,
        START_LAT,
        180.0,              # due south
        PLAYER_OFFSET_M,
    )
    player_point = Point.from_latlng(
        LatLng(player_lat, player_lon),
        terrain,
    )

    # The UK is blue, so blue units earn credit for German losses.
    tracker = LossTrackerConfig(player_coalition="blue")

    # Mission metadata.
    mission.start_time = datetime(1944, 6, 15, 12, 0, 0)
    if len(band_names) == len(BANDS):
        mission.set_sortie_text("Channel Drone Gunnery")
    else:
        levels = " / ".join(name.title() for name in band_names)
        mission.set_sortie_text(f"Channel Drone Gunnery ({levels})")

    altitudes = [f"{BANDS[name]:,} ft" for name in band_names]
    if len(altitudes) == 1:
        formations = f"One German target formation flies a racetrack pattern at {altitudes[0]}.\n"
    else:
        formations = (
            f"{len(altitudes)} German target formations fly racetrack patterns at "
            f"{', '.join(altitudes[:-1])} and {altitudes[-1]}.\n"
        )
    mission.set_description_text(
        "Air-to-air gunnery practice over The Channel.\n\n"
        + formations
        + "Each formation consists of two Bf 109 fighters following one Ju 88 bomber.\n"
        "Targets are set to Weapon Hold and No Reaction to Threat.\n\n"
        "A target counts as destroyed when it is shot down, crashes, its pilot "
        "ejects, or it lands away from a German airfield. Each kill shows the "
        "rounds you fired for it.\n\n"
        f"The mission ends {tracker.end_mission_delay_s:.0f} seconds after the "
        "last target is destroyed or you run out of ammunition, with a summary "
        "on screen. Use the F10 radio menu for the current tally."
    )
    mission.set_description_bluetask_text(
        "Intercept the German drone formations and practice air-to-air gunnery."
    )
    mission.set_description_redtask_text(
        "Passive target aircraft. Do not engage."
    )

    # Player.
    create_player(mission, uk, spitfire, player_point, start)

    # Target altitude bands for this variant.
    for name in band_names:
        create_target_band(
            mission,
            germany,
            bomber,
            fighter,
            name,
            BANDS[name],
            start,
            end,
        )

    # Extended loss scoring, and end the mission once every target is gone.
    add_loss_tracker(mission, tracker)

    # Save.
    mission.save(str(output))


if __name__ == "__main__":
    main()
