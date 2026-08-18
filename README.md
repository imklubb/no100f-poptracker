# Scooby-Doo! Night of 100 Frights - PopTracker Pack

A map tracker for the Scooby-Doo! Night of 100 Frights Archipelago randomizer, with
autotracking support.

PopTracker v0.23.0 or higher is recommended.

## Important Note

This PopTracker was made with the assistance of AI by me `imklubb`.
It's use was for help in coding and lining everything up visually.
Actual design decisions, testing, assets, where created by me.
I feel like it's fair for you to know this, in case that bothers you.

## Installation

1. Download the pack zip from [Releases](https://github.com/imklubb/no100f-poptracker/releases).
2. Place it in your PopTracker `packs` folder.
3. Open PopTracker, click the folder icon, and select the pack.
4. Connect to your Archipelago server using the AP button.

Settings are applied automatically from your slot data when you connect.

Once installed, PopTracker will offer updates on its own - the pack points at
`versions.json` in this repo, so new releases show up without re-downloading by hand.

## Map Legend

Shapes:

- Square - upgrades, keys, monster tokens, and warp gate checks for that room
- Diamond - the individual Scooby Snacks in that room
- Trapezoid below a room - warp gate status (red locked, blue unlocked)
- Small trapezoid above a room - Scooby's current location

The square's snack counter shows how many snacks in that room you can currently
reach, not the room's total.

## Settings

Open the settings window with the settings button in the top left. Everything is
filled in from your YAML on connect, but you can change any of it by hand.

Row 1 - what is randomized:

- Keysanity - off, on, or on with keyrings
- Monster Tokens
- Scooby Snacks
- Warp Rando

Row 2 - your goal:

- Completion Goal - cycles through the eight goal types (vanilla, bosses, tokens,
  snacks, and their combinations)
- Bosses Required
- Monster Tokens Required
- Scooby Snacks Required

Row 3 - display and logic options:

- Warp Gates on Map - show or hide the warp gate markers
- Auto Tab - switch map tabs to follow the area you are in
- Follow Scooby - show a marker on your current room
- Advanced, Expert, CC Early - difficulty options that open extra routes in logic

## Updating the logic when the apworld changes

PopTracker cannot read Archipelago's Python logic at runtime, so `scripts/logic/logic.lua`
is generated from the apworld ahead of time. When a new apworld ships new logic:

```sh
python3 tools/transpile_logic.py path/to/no100f.apworld
python3 tools/verify_logic.py  path/to/no100f.apworld --trials 100   # needs lua5.4
```

The transpiler reads `Regions.py`, `Rules.py` and `Locations.py` and emits one Lua
file covering **every** YAML combination, not just one seed - each rule is recorded
with the option guard that was active when Archipelago applied it.

`verify_logic.py` is the safety net. It loads the apworld's real Python rules with
lightweight Archipelago stubs, runs `set_rules()` for real, and compares region and
location access against the generated Lua running under an actual Lua interpreter,
over hundreds of random option/item combinations. If it prints `PASS`, the tracker
agrees with Archipelago; if it prints `FAIL`, it names the region or location and
the exact state that disagreed.

Two things stay hand-maintained because they describe the *pack*, not the apworld:

- `tools/region_keys.json` - the `$no100f|N` ids that `locations/locations.json` uses.
  Only touch this if you add or rename a room on the map.
- The goal functions at the bottom of the generated file, which mirror the Credits
  rules. They live in `PRELUDE`/`GOAL_BLOCK` inside `tools/transpile_logic.py`.

## Cutting a release

1. Bump `package_version` in `manifest.json`.
2. `git tag v<version> && git push --tags`

The release workflow builds the zip, writes its sha256 into `versions.json` on
`main`, and attaches the zip to a GitHub release. PopTracker picks it up from there.

To build locally without tagging: `python3 tools/build_pack.py`.

## Problems

Feel free to reach out to me `imklubb` or file an issue if you have any non-logic related problems!

## Credits

vgm5, DeltaJordan, imklubb, Claude
