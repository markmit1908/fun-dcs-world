# fun-dcs-world

Small [pydcs](https://github.com/dcs-retribution/pydcs) tools for DCS World:

- **`dcs_maps_to_kmz.py`** exports every DCS terrain to a KMZ for Google Earth. It shows each map's coordinate origin, its bounds, approximate high-detail areas and all airfields.
- **`channel_drone_gunnery.py`** generates **`channel_drone_gunnery.miz`**, a WWII air-to-air gunnery practice mission on The Channel map, plus low-, medium- and high-altitude-only variants.

Both generated files are committed, so you can use them without running any Python.

## Setup

Needs Python 3.9+. It runs on macOS, Linux or Windows; generating files doesn't need DCS.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Terrain KMZ

```bash
python dcs_maps_to_kmz.py --list   # print each terrain's origin and bounds
python dcs_maps_to_kmz.py          # write dcs_world_maps.kmz
```

Open `dcs_world_maps.kmz` in Google Earth. It has four layers:

1. **DCS Cartesian Origins**: where each map's (0,0) point is.
2. **Terrain Bounds**: the map's coordinate area, projected with the terrain's own projection. pydcs has broken bounds for Falklands and Mariana Islands, so those come from `bounds_overrides.json` (estimated from the airfield locations).
3. **High-detail / Focus Regions**: rough hand-drawn rectangles from `detail_regions.json`. They are approximate, not official ED boundaries.
4. **Airfields**: every airfield pydcs knows about.

Options: `--output`, `--detail-regions`, `--bounds-overrides`, `--no-airports`, `--samples-per-edge`.

## Channel drone gunnery mission

```bash
python channel_drone_gunnery.py    # write all ten mission files
```

| File | Target formations |
| --- | --- |
| `channel_drone_gunnery.miz` | 5,000, 12,000 and 20,000 ft |
| `channel_drone_gunnery_low.miz` | 5,000 ft only |
| `channel_drone_gunnery_medium.miz` | 12,000 ft only |
| `channel_drone_gunnery_high.miz` | 20,000 ft only |
| `channel_drone_gunnery_evasive_ju88_{average,good,excellent}.miz` | One evasive, unarmed Ju 88 at 5,000 ft |
| `channel_drone_gunnery_evasive_bf109_{average,good,excellent}.miz` | One evasive, unarmed Bf 109 at 5,000 ft |

The player start is the same in all of them.

In the evasive missions a single target flies the low racetrack on its own. It has no ammunition (a Ju 88's gunners included) and never fires, but it takes evasive action when attacked (DCS's Evade Fire reaction) and then returns to its racetrack. It won't break off for home: returning to base when out of ammo or low on fuel is switched off. The file name gives the AI skill, which controls how hard it evades.

You fly a Spitfire LF Mk IX, starting in the air at 6,000 ft about 2 miles south of the target lane over the Strait of Dover. Three German formations fly a racetrack between 50°50'07"N 1°10'40"E and 51°07'44"N 1°35'53"E. Each formation is one Ju 88 leading two Bf 109 K-4s, flying at 5,000, 12,000 and 20,000 ft. The targets have no ammunition, are set to Weapon Hold and don't react to threats.

A target counts as destroyed when it's shot down, crashes, its pilot ejects, or it lands away from a German airfield. A panel in the top-right corner shows your rounds fired, kills, rounds per kill and score, and each loss is announced there with the rounds you fired for it. Running out of ammunition shows a summary but the mission carries on, so a target you damaged with your last burst still counts if it goes down. The mission ends 30 seconds after the last target is gone, with a summary screen. See [Extended loss scoring](#extended-loss-scoring).

### Required DCS content

- The Channel map
- Spitfire LF Mk IX module
- WWII Assets Pack (the Ju 88 is an asset-pack aircraft)

### Installing the missions in DCS

On the Windows PC with DCS, run:

```bash
python install_missions.py    # copies every .miz here into Saved Games\DCS\Missions
```

It also installs the loss tracker's window hook into `Saved Games\DCS\Scripts\Hooks`. Restart DCS after the first install; `--no-hook` skips it. It does nothing if the Missions folder doesn't exist. Use `--dest` for a different folder, such as `DCS.openbeta\Missions`.

### Installing the mission in DCS manually

DCS only runs on Windows. If you generated the file on another computer, copy `channel_drone_gunnery.miz` to the Windows PC first.

1. Find your DCS Saved Games folder:
   - `C:\Users\<you>\Saved Games\DCS\` for current installs
   - `C:\Users\<you>\Saved Games\DCS.openbeta\` for older installs that used the open beta

   Type `%USERPROFILE%\Saved Games` into the File Explorer address bar to get there quickly.
2. Open the `Missions` folder inside it. Create it if it doesn't exist.
3. Copy `channel_drone_gunnery.miz` (and any variants you want) into that folder.
4. Start DCS and choose **Mission** from the main menu. Browse to the `Missions` folder (it opens in your Saved Games by default), select **channel_drone_gunnery**, and click **OK**.
5. On the slot screen, pick the **Player Spitfire** slot and fly.

To look at or change the mission first, open it with **Mission Editor → File → Open** instead. If you save changes there, rerunning the script will overwrite them, so save your edited copy under a different name.

Any `.miz` file can also be opened from any folder with **Mission → Open**. The `Missions` folder is just the default place DCS looks.

## Tests

```bash
python -m pytest -q tests
```

The tests check the KMZ structure and also the data files: estimated bounds must contain all their airfields, and detail regions must lie inside their terrain.

## Extended loss scoring

`loss_tracker/` is a module you can add to any pydcs mission generator. DCS only scores a kill when an aircraft is destroyed. The tracker also counts an enemy aircraft as lost when it:

- is shot down,
- crashes,
- has its pilot eject, or
- lands anywhere other than an allied airbase, FARP or ship.

The credit goes to the last unit on the player's coalition that hit it within the last 15 minutes. Normal DCS scoring isn't changed. Each loss is announced on screen with the rounds the attacker fired for it and their effective rate at that moment (total rounds fired so far � kills so far), and the F10 radio menu shows the tally.

While flying, each human pilot sees a status panel in the top-right message area, refreshed every second: rounds fired, kills, rounds per kill, the side's score (enemy aircraft credited, and the percentage) and the last few losses. DCS messages can't be pinned, so the panel replaces itself each second, and it repeats the kill messages it would otherwise hide. It pauses while a summary is up and stops at MISSION COMPLETE. Turn it off with `status_enabled=False`.

A full-screen summary lists every enemy aircraft with the time of its loss, how it was lost, who gets the credit, the rounds fired for that kill and the running total, plus each attacker's rounds per kill. It appears when:

| Title | When | Mission |
| --- | --- | --- |
| MISSION COMPLETE | Every enemy aircraft is lost | Ends 30 s later |
| OUT OF AMMO | Every human pilot on the player's side has used up their ammunition | Keeps running; later losses still count (`end_mission_when_out_of_ammo=True` ends it 30 s later) |
| MISSION FAILED | A human pilot's aircraft is lost (crash, ejection, pilot killed) | Keeps running |

With the hook `loss_tracker/LossTrackerGameGUI.lua` installed (see above), the summary opens in a real window with a close button, built from DCS's own notice dialog. In single player the simulation pauses until you close it; the 30-second countdown to the end of the mission resumes after that. The hook also writes every loss and summary into the mission's debriefing log as "comment" events, one per line, and appends each summary to `Saved Games\DCS\Logs\LossTracker.log`, named by the mission's title (`LossTrackerConfig.mission_name`, which defaults to the sortie text; DCS itself only knows the running copy as `tempMission`). Without the hook the summary is a full-screen text message. Mission scripts can't draw windows or write files, which is why this part is a separate hook.

On the debrief screen after the mission, the **Result** box shows the percentage of enemy aircraft credited to your side (for example 67 for 2 of 3). This uses DCS mission goals: the tracker counts credited losses in flag 9002, and `add_loss_tracker` adds one goal per count. Call it after adding the enemy aircraft. Turn it off with `mission_goals=False`.

The rest of the post-mission debrief can't be customised: it's DCS's web-based main menu, which isn't shipped as editable files.

In multiplayer, losing one of several pilots shows "<name> DOWN" instead of MISSION FAILED. DCS has no per-round event for guns, so rounds come from the shooter's ammo count at each kill. Rearming and respawning are allowed for. The summary is also written to `Saved Games\DCS\Logs\dcs.log` on `LossTracker:` lines.

```python
from loss_tracker import LossTrackerConfig, add_loss_tracker

add_loss_tracker(mission, LossTrackerConfig(player_coalition="blue"))
```

`LossTrackerConfig` turns each loss type on or off and sets the timings; see the comments in `loss_tracker/__init__.py`. The script is plain Lua with no MIST or MOOSE dependency, and it's embedded in the `.miz`, so nothing needs installing in DCS.

The tests run the Lua under Lua 5.1 (via `lupa`) against a mock of the DCS scripting API. They check the logic, not DCS itself, so confirm new behaviour in DCS too.
