"""
Extended aircraft loss scoring for pydcs-generated missions.

DCS only scores a kill when an aircraft is destroyed. ``add_loss_tracker``
embeds ``loss_tracker.lua`` in a mission so that an enemy aircraft also counts
as lost, credited to the last player-coalition unit that hit it, when it
crashes, its pilot ejects, or it lands away from an allied base. Losses are
announced on screen and summarised when every enemy aircraft is gone, at which
point the mission can end.

Usage:
    from loss_tracker import LossTrackerConfig, add_loss_tracker

    add_loss_tracker(mission, LossTrackerConfig(player_coalition="blue"))
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import dcs
from dcs import action, condition, triggers

LUA_SCRIPT = Path(__file__).resolve().with_name("loss_tracker.lua")


@dataclass
class LossTrackerConfig:
    # Coalition whose units earn credit; the other one is tracked as enemy.
    player_coalition: str = "blue"

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

    # Set end_flag once every enemy aircraft is lost, and end the mission
    # end_mission_delay_s later when end_mission_when_all_lost is True.
    end_mission_when_all_lost: bool = True
    end_mission_delay_s: float = 30.0
    end_flag: int = 9001

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
                text = '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'
            fields.append(f"    {key} = {text},")
        return "LossTrackerConfig = {\n" + "\n".join(fields) + "\n}\n"


def add_loss_tracker(
    mission: dcs.Mission, config: Optional[LossTrackerConfig] = None
) -> LossTrackerConfig:
    """
    Embed the loss tracker in ``mission``.

    Adds a mission-start trigger that sets ``LossTrackerConfig`` and runs
    ``loss_tracker.lua``, plus, when enabled, a trigger that ends the mission
    once the tracker sets ``config.end_flag``. Returns the config used.
    """
    config = config or LossTrackerConfig()

    start = triggers.TriggerStart(comment="Loss tracker")
    start.add_action(action.DoScript(mission.string(config.to_lua())))
    start.add_action(action.DoScriptFile(mission.map_resource.add_resource_file(LUA_SCRIPT)))
    mission.triggerrules.triggers.append(start)

    if config.end_mission_when_all_lost:
        end = triggers.TriggerOnce(comment="Loss tracker: all enemy aircraft lost")
        end.add_condition(condition.FlagIsTrue(config.end_flag))
        end.add_action(action.EndMission(text=mission.string("All enemy aircraft eliminated.")))
        mission.triggerrules.triggers.append(end)

    return config
