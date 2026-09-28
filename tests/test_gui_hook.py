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
    # One debrief row per non-blank line, since the debrief table's rows are one line high.
    assert gui.debriefing() == [line.strip() for line in SUMMARY.splitlines() if line.strip()]
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
    assert gui.debriefing()[0] == "==========  MISSION COMPLETE  =========="


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


def test_pauses_single_player_until_closed(gui):
    gui.message(SUMMARY)
    assert gui.mock.paused is True
    gui.window.onClose()
    assert gui.window.visible is False
    assert gui.mock.paused is False


def test_does_not_unpause_if_already_paused(gui):
    gui.mock.paused = True
    gui.message(SUMMARY)
    gui.window.onClose()
    assert gui.mock.paused is True


def test_never_pauses_multiplayer(gui):
    gui.mock.multiplayer = True
    gui.message(SUMMARY)
    assert gui.window.visible
    assert gui.mock.paused is False


def test_loss_messages_do_not_pause(gui):
    gui.message(LOSS)
    assert gui.mock.paused is False


def test_history_uses_mission_line(gui, tmp_path):
    summary = SUMMARY.replace(
        "All enemy aircraft eliminated\n",
        "All enemy aircraft eliminated\nMission: Channel Drone Gunnery (Low)\n",
    )
    gui.message(summary)
    history = (tmp_path / "Logs" / "LossTracker.log").read_text()
    assert "  Channel Drone Gunnery (Low)\n" in history
    assert "channel_drone_gunnery_low" not in history


# Debrief screen panel -------------------------------------------------------


def debrief_parts(gui):
    debriefing = gui.lua.globals().package.loaded["debriefing"]
    window = debriefing.window()
    main = window.containerMain
    top = list(main.pTop.children.values())
    added = [w for w in main.children.values() if w.kind == "Panel"]
    return debriefing, main, (top[0] if top else None), (added[0] if added else None)


def report_text(panel):
    return [w.text for w in panel.children.values()]


def test_debrief_panel_shows_summary(gui):
    gui.mock.installDebriefing(True)
    gui.message(SUMMARY)
    debriefing, main, button, panel = debrief_parts(gui)
    debriefing.show(True)

    assert button.text == "EVENT LOG" and button.visible
    assert (button.x, button.y, button.w, button.h) == (1050, 10, 180, 30)
    assert panel.visible and not main.pGrid.visible
    assert (panel.x, panel.y, panel.w, panel.h) == (0, 349, 1280, 378)
    assert panel.skin == "gridPanelSkin"
    assert report_text(panel) == SUMMARY.splitlines()
    assert all(c.skin == "cellSkin" for c in panel.children.values())
    assert any("debrief panel installed" in e.message for e in gui.mock.log.values())


def test_debrief_button_toggles_event_log(gui):
    gui.mock.installDebriefing(True)
    gui.message(SUMMARY)
    debriefing, main, button, panel = debrief_parts(gui)
    button.onChange()
    assert main.pGrid.visible and not panel.visible
    assert button.text == "LOSS TRACKER"
    button.onChange()
    assert panel.visible and not main.pGrid.visible


def test_debrief_panel_added_when_created_later(gui):
    debriefing = gui.mock.installDebriefing(False)
    gui.message(SUMMARY)  # patches the module; no window yet
    debriefing.create()
    debriefing.show(True)
    _, _, button, panel = debrief_parts(gui)
    assert button.visible
    assert report_text(panel)[0] == "==========  MISSION COMPLETE  =========="


def test_debrief_panel_built_once(gui):
    gui.mock.installDebriefing(True)
    gui.message(SUMMARY)
    debriefing, main, _, _ = debrief_parts(gui)
    debriefing.show(True)
    debriefing.show(False)
    debriefing.show(True)
    assert len(main.pTop.children) == 1
    assert len([w for w in main.children.values() if w.kind == "Panel"]) == 1


def test_debrief_losses_without_summary(gui):
    gui.mock.installDebriefing(True)
    gui.message(LOSS)
    gui.hook.onSimulationStop()
    _, _, button, panel = debrief_parts(gui)
    assert button.visible
    text = report_text(panel)
    assert text[1] == "No end-of-mission summary: the mission ended early."
    assert text[-1] == "  " + LOSS


def test_debrief_hidden_without_data(gui):
    gui.mock.installDebriefing(True)
    gui.hook.onSimulationStop()
    debriefing, main, button, panel = debrief_parts(gui)
    debriefing.show(True)
    assert button.visible is False
    assert main.pGrid.visible and not panel.visible


def test_debrief_cleared_on_new_mission(gui):
    gui.mock.installDebriefing(True)
    gui.message(SUMMARY)
    gui.hook.onSimulationStart()
    _, main, button, _ = debrief_parts(gui)
    assert button.visible is False
    assert main.pGrid.visible


def test_debrief_long_report_truncated(gui):
    gui.mock.installDebriefing(True)
    many = SUMMARY + "\n" + "\n".join(f"  line {i}" for i in range(40))
    gui.message(many)
    _, _, _, panel = debrief_parts(gui)
    text = report_text(panel)
    assert len(text) == (378 - 16) // 20
    assert text[-1].startswith("... the rest is in the event log")


def test_debrief_layout_change_is_logged_not_raised(gui):
    gui.mock.installDebriefing(True)
    gui.lua.execute('package.loaded["debriefing"].window().containerMain.pDown = nil')
    gui.message(SUMMARY)
    assert any("debrief panel failed" in e for e in gui.errors())
    assert gui.window.visible  # summary window still shown
