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
python channel_drone_gunnery.py    # write all four mission files
```

| File | Target formations |
| --- | --- |
| `channel_drone_gunnery.miz` | 5,000, 12,000 and 20,000 ft |
| `channel_drone_gunnery_low.miz` | 5,000 ft only |
| `channel_drone_gunnery_medium.miz` | 12,000 ft only |
| `channel_drone_gunnery_high.miz` | 20,000 ft only |

The player start is the same in all four.

You fly a Spitfire LF Mk IX, starting in the air at 6,000 ft about 2 miles south of the target lane over the Strait of Dover. Three German formations fly a racetrack between 50°50'07"N 1°10'40"E and 51°07'44"N 1°35'53"E. Each formation is one Ju 88 leading two Bf 109 K-4s, flying at 5,000, 12,000 and 20,000 ft. The targets have no ammunition, are set to Weapon Hold and don't react to threats.

### Required DCS content

- The Channel map
- Spitfire LF Mk IX module
- WWII Assets Pack (the Ju 88 is an asset-pack aircraft)

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
