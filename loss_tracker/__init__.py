"""
Extended aircraft loss scoring for pydcs-generated missions.

DCS only scores a kill when an aircraft is destroyed. ``add_loss_tracker``
embeds ``loss_tracker.lua`` in a mission so that an enemy aircraft also counts
as lost, credited to the last player-coalition unit that hit it, when it
crashes, its pilot ejects, or it lands away from an allied base. Losses are
announced on screen with the rounds the attacker fired for them. A full-screen
summary appears when every enemy aircraft is gone ("MISSION COMPLETE", after
which the mission can end) or when a player's aircraft is lost ("MISSION
FAILED").

Usage:
    from loss_tracker import LossTrackerConfig, add_loss_tracker

    add_loss_tracker(mission, LossTrackerConfig(player_coalition="blue"))
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import List, Optional
import tempfile

import dcs
from dcs import action, condition, goals, triggers

LUA_SCRIPT = Path(__file__).resolve().with_name("loss_tracker.lua")

# pydcs reads resource files when the mission is saved, so the per-mission
# scripts must outlive add_loss_tracker(). They are removed at exit.
_script_dirs: List[tempfile.TemporaryDirectory] = []


@dataclass
class LossTrackerConfig:
    # Coalition whose units earn credit; the other one is tracked as enemy.
    player_coalition: str = "blue"
    # Shown in summaries and the LossTracker.log history. Empty means the
    # mission's sortie text at the time add_loss_tracker() is called.
    mission_name: str = ""

    # A crash always removes the aircraft; this decides whether the last
    # attacker gets credit for it.
    crash_is_loss: bool = True
    # When False, an ejection is ignored and the aircraft counts once it crashes.
    ejection_is_loss: bool = True
    # Landing anywhere other than an allied airbase, FARP or ship. When False,
    # an aircraft sitting off-field keeps the mission from ending.
    emergency_landing_is_loss: bool = True

    # A hit older than this no longer earns credit.
    attribution_timeout_s: float = 15 * 60
    # Wait this long after a crash/ejection/landing so a following KILL event
    # can claim it as "shot down".
    resolve_delay_s: float = 3.0
    # Landing within this distance of an allied airbase counts as "at the base".
    allied_base_radius_m: float = 3000.0
    # How often to look for aircraft that vanished or stopped on the ground
    # without an event.
    poll_interval_s: float = 5.0
    message_duration_s: int = 10
    # How long the "MISSION FAILED" summary stays up after a player's aircraft
    # is lost. The "MISSION COMPLETE" one stays until the mission ends.
    summary_duration_s: int = 60
    # Status block in the top-right message area for each human pilot:
    # rounds fired, kills, rounds per kill, score and recent losses,
    # refreshed every status_interval_s until a summary is shown.
    status_enabled: bool = True
    status_interval_s: float = 1.0

    # End conditions. When one is met, a summary is shown and the mission
    # ends end_mission_delay_s later (the tracker sets end_flag, which an
    # EndMission trigger watches).
    # Every enemy aircraft is lost ("MISSION COMPLETE").
    end_mission_when_all_lost: bool = True
    # Every human pilot on the player's coalition has used up all their ammo
    # ("OUT OF AMMO"; pilots who started with none don't count). The summary
    # is always shown; by default the mission carries on and losses after it
    # (a target damaged by the last burst going down, say) still count.
    end_mission_when_out_of_ammo: bool = False
    end_mission_delay_s: float = 30.0
    end_flag: int = 9001

    # Mission goals: the debrief's mission result becomes the percentage of
    # enemy aircraft credited to the player's coalition. The tracker keeps
    # that count in score_flag; add_loss_tracker() adds one goal per count.
    mission_goals: bool = True
    score_flag: int = 9002

    def __post_init__(self):
        if self.player_coalition not in ("blue", "red"):
            raise ValueError(
                f"player_coalition must be 'blue' or 'red', got {self.player_coalition!r}"
            )
        for name in (
            "attribution_timeout_s",
            "resolve_delay_s",
            "allied_base_radius_m",
            "poll_interval_s",
            "message_duration_s",
            "summary_duration_s",
            "status_interval_s",
            "end_mission_delay_s",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")

    def to_lua(self) -> str:
        """Return the ``LossTrackerConfig = {...}`` Lua statement for this config."""
        fields = []
        for key, value in asdict(self).items():
            if isinstance(value, bool):
                text = "true" if value else "false"
            elif isinstance(value, (int, float)):
                text = repr(value)
            else:
                text = (
                    '"'
                    + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                    + '"'
                )
            fields.append(f"    {key} = {text},")
        return "LossTrackerConfig = {\n" + "\n".join(fields) + "\n}\n"


def add_loss_tracker(
    mission: dcs.Mission, config: Optional[LossTrackerConfig] = None
) -> LossTrackerConfig:
    """
    Embed the loss tracker in ``mission``.

    Adds a mission-start trigger that runs ``loss_tracker.lua`` with the
    config prepended, plus, when enabled, a trigger that ends the mission once
    the tracker sets ``config.end_flag``. Returns the config used.

    Call it after setting the mission's sortie text, which becomes
    ``mission_name`` unless the config sets one, and after adding the enemy
    aircraft, which the mission goals are sized from.

    The config is baked into the script file rather than passed through a
    DO SCRIPT action: DCS failed to resolve the DO SCRIPT's dictionary text in
    a pydcs-generated mission and ran the key name as Lua instead.
    """
    config = config or LossTrackerConfig()
    if not config.mission_name:
        # DCS only knows the running copy as "tempMission", so bake the name in.
        config = replace(config, mission_name=mission.sortie_text())

    script_dir = tempfile.TemporaryDirectory(prefix="loss_tracker_")
    _script_dirs.append(script_dir)
    script = Path(script_dir.name) / LUA_SCRIPT.name
    # Bytes, so Windows doesn't turn "\n" into "\r\n".
    script.write_bytes(config.to_lua().encode("utf-8") + b"\n" + LUA_SCRIPT.read_bytes())

    start = triggers.TriggerStart(comment="Loss tracker")
    start.add_action(action.DoScriptFile(mission.map_resource.add_resource_file(script)))
    mission.triggerrules.triggers.append(start)

    if config.end_mission_when_all_lost or config.end_mission_when_out_of_ammo:
        end = triggers.TriggerOnce(comment="Loss tracker: end mission")
        end.add_condition(condition.FlagIsTrue(config.end_flag))
        # No end text: it would also be a dictionary lookup.
        end.add_action(action.EndMission())
        mission.triggerrules.triggers.append(end)

    if config.mission_goals:
        add_score_goals(mission, config)

    return config


def enemy_aircraft_count(mission: dcs.Mission, player_coalition: str) -> int:
    """Number of airplane and helicopter units on the coalition opposing the player."""
    enemy = "red" if player_coalition == "blue" else "blue"
    return sum(
        len(group.units)
        for country in mission.coalition[enemy].countries.values()
        for group in country.plane_group + country.helicopter_group
    )


def add_score_goals(mission: dcs.Mission, config: LossTrackerConfig) -> int:
    """
    Add one player-side goal per possible credited-loss count, so the
    debrief's mission result reads as the percentage of enemy aircraft lost.

    Each goal tests score_flag == k rather than >= k, so exactly one is true
    at a time (DCS adds up the scores of all true goals).

    Goals go to OFFLINE, which is what single player uses (DCS's own
    single-player missions put theirs there; BLUE/RED goals were ignored in
    a single-player test), and to the player's coalition for multiplayer.
    Returns the enemy aircraft count.
    """
    total = enemy_aircraft_count(mission, config.player_coalition)
    add_side = mission.goals.add_blue if config.player_coalition == "blue" else mission.goals.add_red
    for add in (mission.goals.add_offline, add_side):
        for k in range(1, total + 1):
            g = goals.Goal(
                comment=f"Loss tracker: {k} of {total} enemy aircraft", score=round(100 * k / total)
            )
            g.rules.append(condition.FlagEquals(config.score_flag, k))
            add(g)
    return total
