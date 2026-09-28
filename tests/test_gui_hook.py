import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
lua51 = pytest.importorskip("lupa.lua51")

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "loss_tracker" / "LossTrackerGameGUI.lua"
MOCK = Path(__file__).resolve().with_name("gui_mock.lua")

SUMMARY = """==========  MISSION COMPLETE  ==========
All enemy aircraft eliminated
Time: 14 min 43 s

Enemy aircraft lost: 2 of 2
  Narj: 2 (500 rounds fired, 250 per kill)
  5:00  LOW Bomber Pilot #1 (Ju-88A4): shot down - Narj (300 rounds, 300 fired so far)
  8:20  LOW Fighters Pilot #1 (Bf-109K-4): shot down - Narj (200 rounds, 500 fired so far)

Mission ends in 30 seconds"""

LOSS = "Ju-88A4 (LOW Bomber Pilot #1) shot down - credited to Narj (300 rounds, 300 fired so far)"


class Gui:
    def __init__(self, writedir):
        self.lua = lua51.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(MOCK.read_text())
        self.g = self.lua.globals()
        self.mock = self.g.GuiMock
        self.mock.writedir = str(writedir) + "\\"
        self.lua.execute(HOOK.read_text())
        self.hook = self.mock.callbacks

    def message(self, text, duration=10, clear=False):
        self.hook.onTriggerMessage(text, duration, clear)

    def frame(self, model_time):
        self.mock.modelTime = model_time
        self.hook.onSimulationFrame()

    @property
    def window(self):
        windows = list(self.mock.windows.values())
        return windows[-1] if windows else None

    def debriefing(self):
        return list(self.mock.debriefing.values())

    def errors(self):
        return [e.message for e in self.mock.log.values() if e.level == 2]


@pytest.fixture
def gui(tmp_path):
    (tmp_path / "Logs").mkdir()
    return Gui(tmp_path)


def test_hook_loads_and_registers(gui):
    assert gui.hook is not None
    assert gui.errors() == []


def test_summary_opens_window(gui):
    gui.message(SUMMARY, 30, True)
    window = gui.window
    assert window.visible
    assert window.text == "MISSION COMPLETE"
    assert window.cbDontShow.visible is False
    assert window.name == "./Scripts/UI/ImportantNoticeDialog.dlg"
    # Centred on a 1920x1080 screen at 1100x620.
    assert (window.x, window.y, window.w, window.h) == (410, 230, 1100, 620)

    widgets = [(w.kind, w.text) for w in window.contentScroll.children.values()]
    assert widgets == [
        ("Static", "All enemy aircraft eliminated"),
        ("Static", "Time: 14 min 43 s"),
        ("EditBox", "\n".join(SUMMARY.splitlines()[4:8])),
        ("Static", "Mission ends in 30 seconds"),
    ]
    ys = [w.y for w in window.contentScroll.children.values()]
    assert ys == sorted(ys) and len(set(ys)) == len(ys)


def test_second_summary_reuses_window(gui):
    gui.message(SUMMARY)
    gui.message(SUMMARY.replace("MISSION COMPLETE", "OUT OF AMMO"))
    assert len(gui.mock.windows) == 1
    assert gui.window.text == "OUT OF AMMO"
    assert len(gui.window.contentScroll.children) == 4


def test_close_button_hides(gui):
    gui.message(SUMMARY)
    gui.window.onClose()
    assert gui.window.visible is False


def test_simulation_stop_hides(gui):
    gui.message(SUMMARY)
    gui.hook.onSimulationStop()
    assert gui.window.visible is False


def test_summary_written_to_debriefing_and_history(gui, tmp_path):
    gui.message(SUMMARY)
    assert gui.debriefing() == [SUMMARY]
    history = (tmp_path / "Logs" / "LossTracker.log").read_text()
    assert "channel_drone_gunnery_low" in history
    assert SUMMARY in history


def test_loss_messages_go_to_debriefing_only(gui, tmp_path):
    gui.message(LOSS)
    gui.message("LOW Fighters (Bf-109K-4) crashed - no credit")
    assert gui.debriefing() == [
        "LossTracker: " + LOSS,
        "LossTracker: LOW Fighters (Bf-109K-4) crashed - no credit",
    ]
    assert gui.window is None
    assert not (tmp_path / "Logs" / "LossTracker.log").exists()


def test_other_messages_ignored(gui):
    gui.message("Welcome to the Channel")
    gui.message("Enemy aircraft lost: 0 of 3\n  Narj: 0")
    assert gui.debriefing() == []
    assert gui.window is None


def test_tells_tracker_in_single_player(gui):
    gui.frame(1)
    assert len(gui.mock.doScripts) == 1
    assert "LossTracker.hookPresent = true" in gui.mock.doScripts[1]
    gui.frame(10)
    assert len(gui.mock.doScripts) == 1  # stops once acknowledged


def test_retries_until_tracker_loaded(gui):
    gui.mock.trackerLoaded = False
    gui.frame(1)
    gui.frame(2)  # throttled to every 5 s
    assert len(gui.mock.doScripts) == 1
    gui.mock.trackerLoaded = True
    gui.frame(7)
    gui.frame(20)
    assert len(gui.mock.doScripts) == 2


def test_never_tells_tracker_in_multiplayer(gui):
    gui.mock.multiplayer = True
    gui.frame(1)
    gui.frame(60)
    assert len(gui.mock.doScripts) == 0


def test_window_failure_is_logged_not_raised(gui):
    gui.lua.execute('package.loaded["DialogLoader"] = nil; package.preload["DialogLoader"] = function() error("no dlg") end')
    gui.message(SUMMARY)
    assert any("window failed" in e for e in gui.errors())
    assert gui.debriefing() == [SUMMARY]


def test_tracker_shortens_summary_when_hook_present():
    from test_loss_tracker import Sim

    sim = Sim()
    player = sim.unit("Spitfire", side=2, player="Mark")
    bandit = sim.unit("Bomber")
    sim.start()
    sim.tracker.hookPresent = True
    sim.kill(player, bandit)
    splash = [m for m in sim.mock.messages.values() if m.clearview][0]
    assert splash.duration == 2
