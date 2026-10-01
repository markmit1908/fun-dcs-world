import sys
import zipfile
from pathlib import Path

import pytest
from dcs import task
from dcs.unit import Skill

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import channel_drone_gunnery as cdg  # noqa: E402


@pytest.fixture(scope="module")
def planes():
    return (
        cdg.find_plane(*cdg.SPITFIRE_IDS),
        cdg.find_plane(*cdg.FIGHTER_IDS),
        cdg.find_plane(*cdg.BOMBER_IDS),
    )


def variant(filename):
    return next(v for v in cdg.VARIANTS if v.filename == filename)


def build(planes, filename):
    with cdg.quiet_dcs_install_lookup():
        return cdg.build_mission(*planes, variant(filename))


def red_groups(mission):
    germany = mission.coalition["red"].countries["Germany"]
    return germany.plane_group


def options(group):
    """{option key: value} set on the first route point, in order, plus the task order."""
    tasks = group.points[0].tasks
    return {t.params["action"]["params"]["name"]: t.params["action"]["params"]["value"]
            for t in tasks if isinstance(t, task.Option)}, [type(t).__name__ for t in tasks]


def test_variant_files():
    names = [v.filename for v in cdg.VARIANTS]
    assert names == [
        "channel_drone_gunnery.miz",
        "channel_drone_gunnery_low.miz",
        "channel_drone_gunnery_medium.miz",
        "channel_drone_gunnery_high.miz",
        "channel_drone_gunnery_evasive_ju88_average.miz",
        "channel_drone_gunnery_evasive_ju88_good.miz",
        "channel_drone_gunnery_evasive_ju88_excellent.miz",
        "channel_drone_gunnery_evasive_bf109_average.miz",
        "channel_drone_gunnery_evasive_bf109_good.miz",
        "channel_drone_gunnery_evasive_bf109_excellent.miz",
        "channel_drone_gunnery_armed_bf109_average.miz",
        "channel_drone_gunnery_armed_bf109_good.miz",
        "channel_drone_gunnery_armed_bf109_excellent.miz",
    ]


@pytest.mark.parametrize("skill", [Skill.Average, Skill.Good, Skill.Excellent])
def test_armed_bf109_returns_fire(planes, skill):
    mission = build(planes, f"channel_drone_gunnery_armed_bf109_{skill.value.lower()}.miz")
    groups = red_groups(mission)
    assert len(groups) == 1 and len(groups[0].units) == 1
    group = groups[0]
    unit = group.units[0]
    assert group.name == "Armed Bf-109"
    assert unit.type == "Bf-109K-4"
    assert unit.skill == skill
    assert unit.gun == 100  # guns kept
    assert unit.pylons == {}

    values, order = options(group)
    assert values == {
        task.OptROE.Key: task.OptROE.Values.ReturnFire,
        task.OptReactOnThreat.Key: task.OptReactOnThreat.Values.EvadeFire,
        task.OptRTBOnOutOfAmmo.Key: task.OptRTBOnOutOfAmmo.Values.NoWeapon,
        task.OptRTBOnBingoFuel.Key: False,
    }
    assert order[-1] == "OrbitAction"
    assert group.points[0].alt == int(cdg.LOW_FT * cdg.FT_TO_M)
    assert mission.sortie_text() == f"Channel Drone Gunnery (Armed Bf-109, {skill.value})"
    assert "shoot back once you have fired at it" in mission.description_text()
    assert [g.score for g in mission.goals.goals["offline"]] == [100]


def test_all_single_target_missions_start_at_same_position(planes):
    positions = set()
    for v in cdg.VARIANTS:
        if v.evasive:
            mission = build(planes, v.filename)
            point = red_groups(mission)[0].points[0].position
            positions.add((round(point.x, 3), round(point.y, 3)))
    assert len(positions) == 1


@pytest.mark.parametrize("target,type_id", [("ju88", "Ju-88A4"), ("bf109", "Bf-109K-4")])
@pytest.mark.parametrize("skill", [Skill.Average, Skill.Good, Skill.Excellent])
def test_evasive_single_target(planes, target, type_id, skill):
    mission = build(planes, f"channel_drone_gunnery_evasive_{target}_{skill.value.lower()}.miz")
    groups = red_groups(mission)
    assert len(groups) == 1
    group = groups[0]
    assert len(group.units) == 1
    unit = group.units[0]
    assert unit.type == type_id
    assert unit.skill == skill
    assert (unit.gun, unit.pylons, unit.chaff, unit.flare) == (0, {}, 0, 0)
    assert group.frequency == unit.unit_type.radio_frequency

    values, order = options(group)
    assert values == {
        task.OptROE.Key: task.OptROE.Values.WeaponHold,
        task.OptReactOnThreat.Key: task.OptReactOnThreat.Values.EvadeFire,
        task.OptRTBOnOutOfAmmo.Key: task.OptRTBOnOutOfAmmo.Values.NoWeapon,
        task.OptRTBOnBingoFuel.Key: False,
    }
    # Options before the orbit, so DCS applies them before it starts the racetrack.
    assert order[-1] == "OrbitAction"
    assert group.points[0].alt == int(cdg.LOW_FT * cdg.FT_TO_M)

    name = cdg.EVASIVE_TARGETS[target]
    assert mission.sortie_text() == f"Channel Drone Gunnery (Evasive {name}, {skill.value})"
    assert f"A single unarmed {name}" in mission.description_text()
    # One target: the Result box goes straight to 100 on the kill.
    assert [g.score for g in mission.goals.goals["offline"]] == [100]


def test_passive_formations_unchanged(planes):
    mission = build(planes, "channel_drone_gunnery_low.miz")
    groups = red_groups(mission)
    assert [len(g.units) for g in groups] == [1, 2]
    for group in groups:
        values, _ = options(group)
        assert values[task.OptReactOnThreat.Key] == task.OptReactOnThreat.Values.NoReaction
        assert all(u.skill == Skill.Average for u in group.units)
    assert mission.sortie_text() == "Channel Drone Gunnery (Low)"
    assert [g.score for g in mission.goals.goals["offline"]] == [33, 67, 100]


def test_evasive_mission_saves_with_tracker(planes, tmp_path):
    mission = build(planes, "channel_drone_gunnery_evasive_bf109_good.miz")
    path = tmp_path / "evasive.miz"
    mission.save(str(path))
    with zipfile.ZipFile(path) as miz:
        script = miz.read("l10n/DEFAULT/loss_tracker.lua").decode()
    assert 'mission_name = "Channel Drone Gunnery (Evasive Bf-109, Good)"' in script
