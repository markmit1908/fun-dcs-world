import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loss_tracker import LUA_SCRIPT, LossTrackerConfig, add_loss_tracker  # noqa: E402

lua51 = pytest.importorskip("lupa.lua51")

MOCK = Path(__file__).resolve().with_name("dcs_mock.lua")
RED, BLUE = 1, 2
TIMEOUT = LossTrackerConfig().attribution_timeout_s


class Sim:
    """Runs loss_tracker.lua under Lua 5.1 against the DCS mock."""

    def __init__(self):
        self.lua = lua51.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(MOCK.read_text())
        self.g = self.lua.globals()
        self.mock = self.g.Mock
        self.events = self.g.world.event

    def unit(self, name, side=RED, **opts):
        return self.mock.addUnit(name, self.lua.table_from(dict(opts, side=side)))

    def airbase(self, name, side, x=0, z=0):
        return self.mock.addAirbase(name, side, x, z)

    def start(self, config=None):
        self.config = config or LossTrackerConfig()
        self.lua.execute(self.config.to_lua())
        self.lua.execute(LUA_SCRIPT.read_text())
        self.tracker = self.g.LossTracker

    def fire(self, event_name, **fields):
        fields["id"] = self.events[event_name]
        self.mock.fire(self.lua.table_from(fields))

    def hit(self, attacker, target):
        self.fire("S_EVENT_HIT", initiator=attacker, target=target)

    def kill(self, killer, target):
        self.fire("S_EVENT_KILL", initiator=killer, target=target)

    def crash(self, unit):
        unit.alive = False
        self.fire("S_EVENT_CRASH", initiator=unit)

    def eject(self, unit):
        self.fire("S_EVENT_EJECTION", initiator=unit)

    def land(self, unit, place=None):
        unit.in_air = False
        unit.speed = 0
        if place is None:
            self.fire("S_EVENT_LAND", initiator=unit)
        else:
            self.fire("S_EVENT_LAND", initiator=unit, place=place)

    def advance(self, seconds=10):
        self.mock.advance(seconds)

    def result(self, name):
        """(reason, credited_to) for a resolved aircraft, None while still active."""
        state = self.tracker.aircraft[name]
        if not state.resolved:
            return None
        return (state.reason, state.creditedTo)

    def resolved_count(self):
        return sum(1 for s in self.tracker.order.values() if s.resolved)

    @property
    def end_flag(self):
        return self.mock.flags[self.config.end_flag]

    def messages(self):
        return [m.text for m in self.mock.messages.values()]

    def splashes(self):
        """Full-screen (clearview) summaries."""
        return [m.text for m in self.mock.messages.values() if m.clearview]

    def shoot(self, unit, rounds_left):
        """One burst: SHOOTING_START, ammo drops to rounds_left, SHOOTING_END."""
        self.fire("S_EVENT_SHOOTING_START", initiator=unit)
        unit.rounds = rounds_left
        self.fire("S_EVENT_SHOOTING_END", initiator=unit)


@pytest.fixture
def sim():
    s = Sim()
    s.player = s.unit("Spitfire", side=BLUE, player="Mark", type="SpitfireLFMkIX")
    s.bandit = s.unit("Bomber")
    return s


def test_shot_down_normally_counts_once(sim):
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.kill(sim.player, sim.bandit)
    sim.crash(sim.bandit)
    sim.fire("S_EVENT_DEAD", initiator=sim.bandit)
    sim.advance()
    assert sim.result("Bomber") == ("kill", "Mark")
    assert sim.resolved_count() == 1
    assert sum("Bomber" in m and "credited" in m for m in sim.messages()) == 1


def test_kill_after_crash_within_delay_is_shot_down(sim):
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.crash(sim.bandit)
    sim.advance(1)
    sim.kill(sim.player, sim.bandit)
    sim.advance()
    assert sim.result("Bomber") == ("kill", "Mark")


def test_kill_after_delay_does_not_count_twice(sim):
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.crash(sim.bandit)
    sim.advance()
    sim.kill(sim.player, sim.bandit)
    assert sim.result("Bomber") == ("crash", "Mark")
    assert sim.resolved_count() == 1


def test_hit_then_crash(sim):
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.crash(sim.bandit)
    sim.advance()
    assert sim.result("Bomber") == ("crash", "Mark")


def test_hit_then_emergency_landing(sim):
    sim.airbase("Enemy field", RED, x=100_000)
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.land(sim.bandit)
    sim.advance()
    assert sim.result("Bomber") == ("emergency_landing", "Mark")


def test_landing_at_enemy_airbase_is_emergency_landing(sim):
    field = sim.airbase("Player field", BLUE)
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.land(sim.bandit, place=field)
    sim.advance()
    assert sim.result("Bomber") == ("emergency_landing", "Mark")


def test_debrief_example_low_band(sim):
    """Replays a real low-band flight: two ejections, then the last Bf 109
    lands at Manston (neutral in the generated mission) 200 s after its last hit."""
    manston = sim.airbase("Manston", 0)
    fighter1 = sim.unit("LOW Fighters", type="Bf-109K-4")
    fighter2 = sim.unit("LOW Fighters #2", type="Bf-109K-4")
    sim.start()

    sim.hit(sim.player, sim.bandit)
    sim.advance(158)
    sim.eject(sim.bandit)
    sim.kill(sim.player, sim.bandit)
    sim.advance(27)
    sim.crash(sim.bandit)

    sim.hit(sim.player, fighter1)
    sim.eject(fighter1)
    sim.kill(sim.player, fighter1)
    sim.advance(17)
    sim.crash(fighter1)

    sim.hit(sim.player, fighter2)
    sim.advance(200)
    sim.land(fighter2, place=manston)
    sim.advance(10)

    assert sim.result("Bomber") == ("kill", "Mark")
    assert sim.result("LOW Fighters") == ("kill", "Mark")
    assert sim.result("LOW Fighters #2") == ("emergency_landing", "Mark")
    sim.advance(sim.config.end_mission_delay_s)
    assert sim.end_flag is True


def test_hit_then_eject_then_crash_counts_once(sim):
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.eject(sim.bandit)
    sim.advance(1)
    sim.crash(sim.bandit)
    sim.advance()
    assert sim.result("Bomber") == ("ejection", "Mark")
    assert sim.resolved_count() == 1


def test_damaged_landing_at_allied_base_is_not_a_loss(sim):
    field = sim.airbase("Home", RED)
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.land(sim.bandit, place=field)
    sim.advance(60)
    assert sim.result("Bomber") is None


def test_landing_near_allied_base_without_place_is_not_a_loss(sim):
    sim.airbase("Home", RED, x=1_000)
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.land(sim.bandit)
    sim.advance(60)
    assert sim.result("Bomber") is None


def test_returned_aircraft_that_despawns_gets_no_credit(sim):
    field = sim.airbase("Home", RED)
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.land(sim.bandit, place=field)
    sim.bandit.alive = False
    sim.advance(60)
    assert sim.result("Bomber") == ("returned", None)


def test_undamaged_emergency_landing_gets_no_credit(sim):
    sim.start()
    sim.land(sim.bandit)
    sim.advance()
    assert sim.result("Bomber") == ("emergency_landing", None)


def test_undamaged_crash_gets_no_credit(sim):
    sim.start()
    sim.crash(sim.bandit)
    sim.advance()
    assert sim.result("Bomber") == ("crash", None)


def test_friendly_fire_gets_no_credit(sim):
    wingman = sim.unit("Enemy wingman")
    sim.start()
    sim.hit(wingman, sim.bandit)
    sim.land(sim.bandit)
    sim.advance()
    assert sim.result("Bomber") == ("emergency_landing", None)


def test_friendly_fire_does_not_replace_player_attacker(sim):
    wingman = sim.unit("Enemy wingman")
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.hit(wingman, sim.bandit)
    sim.crash(sim.bandit)
    sim.advance()
    assert sim.result("Bomber") == ("crash", "Mark")


def test_multiple_attackers_last_one_gets_credit(sim):
    other = sim.unit("Spitfire 2", side=BLUE, player="Alex")
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.hit(other, sim.bandit)
    sim.crash(sim.bandit)
    sim.advance()
    assert sim.result("Bomber") == ("crash", "Alex")


def test_ai_attacker_is_credited_by_unit_name(sim):
    ai = sim.unit("Blue AI", side=BLUE)
    sim.start()
    sim.hit(ai, sim.bandit)
    sim.crash(sim.bandit)
    sim.advance()
    assert sim.result("Bomber") == ("crash", "Blue AI")


def test_attribution_expires(sim):
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.advance(TIMEOUT + 60)
    sim.crash(sim.bandit)
    sim.advance()
    assert sim.result("Bomber") == ("crash", None)


def test_kill_event_without_killer_uses_last_attacker(sim):
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.fire("S_EVENT_KILL", target=sim.bandit)
    assert sim.result("Bomber") == ("kill", "Mark")


def test_player_losses_are_not_tracked(sim):
    sim.start()
    sim.hit(sim.bandit, sim.player)
    sim.crash(sim.player)
    sim.advance()
    assert "Spitfire" not in sim.tracker.aircraft


def test_ejection_ignored_when_disabled(sim):
    sim.start(LossTrackerConfig(ejection_is_loss=False))
    sim.hit(sim.player, sim.bandit)
    sim.eject(sim.bandit)
    sim.advance()
    assert sim.result("Bomber") is None
    sim.crash(sim.bandit)
    sim.advance()
    assert sim.result("Bomber") == ("crash", "Mark")


def test_crash_without_credit_when_disabled(sim):
    sim.start(LossTrackerConfig(crash_is_loss=False))
    sim.hit(sim.player, sim.bandit)
    sim.crash(sim.bandit)
    sim.advance()
    assert sim.result("Bomber") == ("crash", None)


def test_emergency_landing_ignored_when_disabled(sim):
    sim.start(LossTrackerConfig(emergency_landing_is_loss=False))
    sim.hit(sim.player, sim.bandit)
    sim.land(sim.bandit)
    sim.advance(60)
    assert sim.result("Bomber") is None


def test_poll_detects_stopped_on_ground_without_land_event(sim):
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.bandit.in_air = False
    sim.bandit.speed = 0
    sim.advance(20)
    assert sim.result("Bomber") == ("emergency_landing", "Mark")


def test_poll_detects_vanished_aircraft(sim):
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.bandit.alive = False
    sim.advance(20)
    assert sim.result("Bomber") == ("lost", "Mark")


def test_parked_aircraft_is_not_an_emergency_landing(sim):
    sim.unit("Parked", in_air=False, speed=0)
    sim.start()
    sim.advance(60)
    assert sim.result("Parked") is None


def test_late_activation_units_register_on_birth(sim):
    late = sim.unit("Late", active=False)
    sim.start()
    assert "Late" not in sim.tracker.aircraft
    late.active = True
    sim.fire("S_EVENT_BIRTH", initiator=late)
    assert "Late" in sim.tracker.aircraft


def test_helicopters_are_tracked(sim):
    sim.unit("Helo", category=1)
    sim.start()
    assert "Helo" in sim.tracker.aircraft


def test_ground_units_are_not_tracked(sim):
    sim.unit("Tank", category=2)
    sim.start()
    assert "Tank" not in sim.tracker.aircraft


def test_end_flag_after_all_enemies_lost(sim):
    second = sim.unit("Fighter")
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.kill(sim.player, sim.bandit)
    sim.advance(60)
    assert sim.end_flag is None

    sim.hit(sim.player, second)
    sim.land(second)
    sim.advance(10)
    assert sim.end_flag is None  # waits end_mission_delay_s
    sim.advance(sim.config.end_mission_delay_s)
    assert sim.end_flag is True
    summary = [m for m in sim.splashes() if "MISSION COMPLETE" in m]
    assert len(summary) == 1
    assert "All enemy aircraft eliminated" in summary[0]
    assert "Enemy aircraft lost: 2 of 2" in summary[0]
    assert "Mark: 2" in summary[0]
    assert "Mission ends in 30 seconds" in summary[0]


def test_uncredited_losses_still_end_mission(sim):
    sim.start()
    sim.crash(sim.bandit)
    sim.advance(60)
    assert sim.end_flag is True


def test_event_handler_survives_bad_objects(sim):
    sim.start()
    sim.fire("S_EVENT_HIT", initiator=sim.lua.table_from({}), target=sim.lua.table_from({}))
    sim.fire("S_EVENT_CRASH")
    sim.hit(sim.player, sim.bandit)
    sim.crash(sim.bandit)
    sim.advance()
    assert sim.result("Bomber") == ("crash", "Mark")


def test_tally_menu(sim):
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.kill(sim.player, sim.bandit)
    menu = sim.mock.menus[1]
    assert menu.side == BLUE
    menu.fn()
    assert "Enemy aircraft lost: 1 of 1\n  Mark: 1" in sim.messages()[-1]


def test_red_player_coalition(sim):
    sim.start(LossTrackerConfig(player_coalition="red"))
    assert "Spitfire" in sim.tracker.aircraft
    assert "Bomber" not in sim.tracker.aircraft


# Summary screens ------------------------------------------------------------


def test_player_crash_shows_mission_failed_once(sim):
    sim.unit("Other bandit")  # keeps the mission from completing
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.kill(sim.player, sim.bandit)
    sim.advance(125)
    sim.eject(sim.player)
    sim.crash(sim.player)
    sim.fire("S_EVENT_PILOT_DEAD", initiator=sim.player)
    failed = [m for m in sim.splashes() if "MISSION FAILED" in m]
    assert len(failed) == 1
    assert "Mark ejected" in failed[0]
    assert "Time: 2 min 05 s" in failed[0]
    assert "0:00  Bomber (Ju-88A4): shot down - Mark" in failed[0]
    assert sim.end_flag is None


def test_ai_wingman_loss_shows_no_summary(sim):
    wingman = sim.unit("Blue AI", side=BLUE)
    sim.start()
    sim.crash(wingman)
    assert sim.splashes() == []


def test_one_of_two_players_down(sim):
    sim.unit("Spitfire 2", side=BLUE, player="Alex")
    sim.start()
    sim.crash(sim.player)
    assert [m.splitlines()[0] for m in sim.splashes()] == ["==========  Mark DOWN  =========="]


def test_no_failure_summary_after_mission_complete(sim):
    sim.start()
    sim.kill(sim.player, sim.bandit)
    sim.crash(sim.player)
    assert len(sim.splashes()) == 1
    assert "MISSION COMPLETE" in sim.splashes()[0]


# Rounds fired -----------------------------------------------------------------


@pytest.fixture
def armed(sim):
    sim.player.rounds = 1000
    sim.second = sim.unit("Fighter", type="Bf-109K-4")
    sim.start()
    return sim


def rounds(sim, name):
    state = sim.tracker.aircraft[name]
    return (state.rounds, state.roundsTotal)


def test_rounds_per_kill(armed):
    sim = armed
    sim.shoot(sim.player, 800)
    sim.hit(sim.player, sim.bandit)
    sim.kill(sim.player, sim.bandit)
    sim.shoot(sim.player, 500)
    sim.shoot(sim.player, 400)
    sim.hit(sim.player, sim.second)
    sim.crash(sim.second)
    sim.advance()

    assert rounds(sim, "Bomber") == (200, 200)
    assert rounds(sim, "Fighter") == (400, 600)
    assert any("credited to Mark (200 rounds, 200 fired so far)" in m for m in sim.messages())
    complete = [m for m in sim.splashes() if "MISSION COMPLETE" in m][0]
    assert "Mark: 2 (600 rounds fired, 300 per kill)" in complete
    assert "Fighter (Bf-109K-4): crashed - Mark (400 rounds, 600 fired so far)" in complete


def test_rounds_read_live_at_kill_time(armed):
    sim = armed
    sim.fire("S_EVENT_SHOOTING_START", initiator=sim.player)
    sim.player.rounds = 900
    sim.hit(sim.player, sim.bandit)
    sim.kill(sim.player, sim.bandit)
    assert rounds(sim, "Bomber") == (100, 100)


def test_rounds_survive_rearm(armed):
    sim = armed
    sim.shoot(sim.player, 700)
    sim.player.rounds = 1000  # rearmed
    sim.shoot(sim.player, 900)
    sim.hit(sim.player, sim.bandit)
    sim.kill(sim.player, sim.bandit)
    assert rounds(sim, "Bomber") == (400, 400)


def test_rounds_use_last_known_when_shooter_dead(armed):
    sim = armed
    sim.shoot(sim.player, 750)
    sim.hit(sim.player, sim.bandit)
    sim.player.alive = False
    sim.crash(sim.bandit)
    sim.advance()
    assert rounds(sim, "Bomber") == (250, 250)


def test_rounds_carry_over_to_respawned_player(armed):
    sim = armed
    sim.shoot(sim.player, 600)
    sim.player.alive = False
    respawn = sim.unit("Spitfire 2", side=BLUE, player="Mark", rounds=1000)
    sim.shoot(respawn, 900)
    sim.hit(respawn, sim.bandit)
    sim.kill(respawn, sim.bandit)
    assert rounds(sim, "Bomber") == (500, 500)


def test_kill_without_shooting_has_no_rounds(sim):
    sim.start()
    sim.hit(sim.player, sim.bandit)
    sim.kill(sim.player, sim.bandit)
    assert rounds(sim, "Bomber") == (None, None)
    assert any(m.endswith("credited to Mark") for m in sim.messages())


# Out of ammo ------------------------------------------------------------------


def test_out_of_ammo_ends_mission(armed):
    sim = armed
    sim.shoot(sim.player, 600)
    sim.hit(sim.player, sim.bandit)
    sim.kill(sim.player, sim.bandit)
    sim.shoot(sim.player, 0)
    out = [m for m in sim.splashes() if "OUT OF AMMO" in m]
    assert len(out) == 1
    assert "Mark is out of ammunition" in out[0]
    assert "Enemy aircraft lost: 1 of 2" in out[0]
    assert "Mission ends in 30 seconds" in out[0]
    sim.advance(sim.config.end_mission_delay_s - 1)
    assert sim.end_flag is None
    sim.advance(2)
    assert sim.end_flag is True


def test_out_of_ammo_found_by_poll(armed):
    sim = armed
    sim.fire("S_EVENT_SHOOTING_START", initiator=sim.player)
    sim.player.rounds = 0  # no SHOOTING_END
    sim.advance(10)
    assert any("OUT OF AMMO" in m for m in sim.splashes())


def test_unarmed_player_never_out_of_ammo(sim):
    sim.start()
    sim.advance(60)
    assert sim.splashes() == []
    assert sim.end_flag is None


def test_out_of_ammo_waits_for_every_player(armed):
    sim = armed
    other = sim.unit("Spitfire 2", side=BLUE, player="Alex", rounds=1000)
    sim.advance(10)
    sim.shoot(sim.player, 0)
    sim.advance(10)
    assert sim.splashes() == []
    sim.shoot(other, 0)
    assert len(sim.splashes()) == 1
    assert " are out of ammunition" in sim.splashes()[0]


def test_out_of_ammo_disabled(sim):
    sim.player.rounds = 1000
    sim.start(LossTrackerConfig(end_mission_when_out_of_ammo=False))
    sim.shoot(sim.player, 0)
    sim.advance(60)
    assert sim.splashes() == []
    assert sim.end_flag is None


def test_complete_after_out_of_ammo_does_not_reschedule(armed):
    sim = armed
    sim.shoot(sim.player, 500)
    sim.hit(sim.player, sim.bandit)
    sim.kill(sim.player, sim.bandit)
    sim.hit(sim.player, sim.second)
    sim.shoot(sim.player, 0)
    sim.advance(10)
    sim.crash(sim.second)
    sim.advance(5)
    complete = [m for m in sim.splashes() if "MISSION COMPLETE" in m]
    assert len(complete) == 1
    assert "Mission ends in" not in complete[0]
    sim.advance(sim.config.end_mission_delay_s)
    assert sim.end_flag is True


# Python side --------------------------------------------------------------


def test_config_validation():
    with pytest.raises(ValueError):
        LossTrackerConfig(player_coalition="green")
    with pytest.raises(ValueError):
        LossTrackerConfig(resolve_delay_s=0)


def test_config_to_lua_round_trips():
    config = LossTrackerConfig(ejection_is_loss=False, attribution_timeout_s=120)
    lua = lua51.LuaRuntime()
    lua.execute(config.to_lua())
    table = lua.globals().LossTrackerConfig
    assert table.ejection_is_loss is False
    assert table.attribution_timeout_s == 120
    assert table.player_coalition == "blue"


def test_mission_embeds_script_and_end_trigger(tmp_path):
    import dcs
    from dcs.terrain import Caucasus

    mission = dcs.Mission(Caucasus())
    add_loss_tracker(mission)
    path = tmp_path / "tracked.miz"
    mission.save(str(path))

    with zipfile.ZipFile(path) as miz:
        script = miz.read("l10n/DEFAULT/loss_tracker.lua").decode()
        text = miz.read("mission").decode()

    # Config is baked into the script: DCS mis-resolved DO SCRIPT dictionary text.
    config = LossTrackerConfig().to_lua()
    assert script.startswith(config)
    assert script.endswith(LUA_SCRIPT.read_bytes().decode("utf-8"))
    assert "a_do_script_file" in text
    assert "a_do_script(" not in text
    assert "a_end_mission" in text
    assert "c_flag_is_true(9001)" in text

    # The embedded script runs as one chunk and picks up the config.
    sim = Sim()
    sim.lua.execute(script)
    assert sim.g.LossTracker.config.end_flag == 9001


def test_embedded_config_overrides_defaults(tmp_path):
    import dcs
    from dcs.terrain import Caucasus

    mission = dcs.Mission(Caucasus())
    add_loss_tracker(mission, LossTrackerConfig(player_coalition="red", end_flag=42))
    path = tmp_path / "tracked.miz"
    mission.save(str(path))
    with zipfile.ZipFile(path) as miz:
        script = miz.read("l10n/DEFAULT/loss_tracker.lua").decode()

    sim = Sim()
    sim.lua.execute(script)
    tracker = sim.g.LossTracker
    assert tracker.playerSide == RED
    assert tracker.config.end_flag == 42


def test_mission_without_end_trigger():
    import dcs
    from dcs.terrain import Caucasus

    mission = dcs.Mission(Caucasus())
    add_loss_tracker(
        mission,
        LossTrackerConfig(end_mission_when_all_lost=False, end_mission_when_out_of_ammo=False),
    )
    assert len(mission.triggerrules.triggers) == 1


def test_mission_with_only_out_of_ammo_end():
    import dcs
    from dcs.terrain import Caucasus

    mission = dcs.Mission(Caucasus())
    add_loss_tracker(mission, LossTrackerConfig(end_mission_when_all_lost=False))
    assert len(mission.triggerrules.triggers) == 2


def test_summary_names_mission():
    sim = Sim()
    player = sim.unit("Spitfire", side=BLUE, player="Mark")
    bandit = sim.unit("Bomber")
    sim.start(LossTrackerConfig(mission_name='Channel "Drone" Gunnery\n(Low)'))
    sim.kill(player, bandit)
    lines = sim.splashes()[0].splitlines()
    assert lines[1:3] == ["All enemy aircraft eliminated", 'Mission: Channel "Drone" Gunnery']
    assert lines[3] == "(Low)"


def test_summary_without_mission_name(sim):
    sim.start()
    sim.kill(sim.player, sim.bandit)
    assert "Mission:" not in sim.splashes()[0]


def test_mission_name_defaults_to_sortie_text(tmp_path):
    import dcs
    from dcs.terrain import Caucasus

    mission = dcs.Mission(Caucasus())
    mission.set_sortie_text("Channel Drone Gunnery (Low)")
    config = add_loss_tracker(mission)
    assert config.mission_name == "Channel Drone Gunnery (Low)"

    kept = add_loss_tracker(dcs.Mission(Caucasus()), LossTrackerConfig(mission_name="Custom"))
    assert kept.mission_name == "Custom"
