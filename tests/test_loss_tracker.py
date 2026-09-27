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
    summary = [m for m in sim.messages() if m.startswith("All enemy aircraft eliminated")]
    assert len(summary) == 1
    assert "Enemy aircraft lost: 2 of 2" in summary[0]
    assert "Mark: 2" in summary[0]


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
        names = miz.namelist()
        assert "l10n/DEFAULT/loss_tracker.lua" in names
        assert miz.read("l10n/DEFAULT/loss_tracker.lua") == LUA_SCRIPT.read_bytes()
        text = miz.read("mission").decode()
        dictionary = miz.read("l10n/DEFAULT/dictionary").decode()

    assert "a_do_script_file" in text
    assert "a_do_script" in text
    assert "a_end_mission" in text
    assert "c_flag_is_true" in text
    assert "LossTrackerConfig" in dictionary


def test_mission_without_end_trigger():
    import dcs
    from dcs.terrain import Caucasus

    mission = dcs.Mission(Caucasus())
    add_loss_tracker(mission, LossTrackerConfig(end_mission_when_all_lost=False))
    assert len(mission.triggerrules.triggers) == 1
