import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install_missions  # noqa: E402


@pytest.fixture
def missions(tmp_path):
    folder = tmp_path / "DCS" / "Missions"
    folder.mkdir(parents=True)
    return folder


def test_copies_every_miz(missions):
    assert install_missions.main(["--dest", str(missions)]) == 0
    expected = sorted(p.name for p in install_missions.SOURCE_DIR.glob("*.miz"))
    assert expected
    assert sorted(p.name for p in missions.iterdir()) == expected
    for name in expected:
        assert (missions / name).read_bytes() == (install_missions.SOURCE_DIR / name).read_bytes()


def test_installs_gui_hook(missions):
    install_missions.main(["--dest", str(missions)])
    hook = missions.parent / "Scripts" / "Hooks" / "LossTrackerGameGUI.lua"
    assert hook.read_bytes() == install_missions.HOOK.read_bytes()


def test_no_hook_option(missions):
    install_missions.main(["--dest", str(missions), "--no-hook"])
    assert not (missions.parent / "Scripts").exists()


def test_skips_missing_folder(tmp_path, capsys):
    missing = tmp_path / "nope"
    assert install_missions.main(["--dest", str(missing)]) == 0
    assert not missing.exists()
    assert not (tmp_path / "Scripts").exists()
    assert "Skipped" in capsys.readouterr().out
