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

## Layouts: portrait mode, and what PopTracker can and cannot do

PopTracker picks the root layout by the window's **aspect ratio**, nothing else.
From its source (`src/ui/defaulttrackerwindow.cpp`):

```cpp
std::string layout = _aspectRatio > 1 ? "tracker_horizontal"
                   : _aspectRatio < 1 ? "tracker_vertical"
                   : "tracker_default";
```

If the preferred name is missing it falls back to `tracker_default`. The ratio is
recomputed every frame, so the layout swaps live the moment the window crosses
square. This pack defines two roots in `layouts/tracker.json`:

- `tracker_default` - wider-than-tall windows (there is no `tracker_horizontal`).
  One narrow column of items on the left, map filling the rest.
- `tracker_vertical` - taller-than-wide windows. The map fills the top and a
  compact band of items sits along the bottom.

In `tracker_vertical` the band carries `"dock": "bottom"` and the map has **no**
`dock` key at all, which makes it a *floater* that absorbs all remaining space.
The band's height is therefore the only thing deciding how big the map gets.

### Two things that are NOT possible

1. **Icons cannot scale continuously as you resize.** `item_size` is a fixed pixel
   value baked into each node; PopTracker lays items out at `x * item_size` and
   never scales them to fit. The only size change available is swapping layouts at
   the square boundary.
2. **There is no "window is small" trigger**, and Lua cannot read the window size,
   so a script cannot swap layouts by size either.

The map is the exception: PopTracker picks its base scale "so that at 1x zoom, the
whole image fits inside the widget", so giving the map a bigger box is all that is
needed to make it bigger.

### Why the compact grids are generated

`item_size` is read from each node's own JSON and is **not inherited from a
parent**, so a compact layout needs its own copy of every grid. Rather than keep
two copies by hand and let them drift, `layouts/compact.json` is generated:

```sh
python3 tools/make_compact_layouts.py
```

Re-run after editing any normal grid. It rescales `item_size` (64 -> 32, 32 -> 24)
and prints a fit report with the band's measured size.

One non-obvious result from that measuring: **a group's width is set by its header
text, not its icon.** The title label carries 11px of margin on each side, so a key
group is ~65px wide whether its icon is 32px or 24px. Shrinking those icons buys
height, not width.

## Keys: grouped by map, with nameplates

Keys are grouped by the map their door is on, which is how you think about them
while playing:

| group | n | keys |
|-------|---|------|
| Hub | 2 | Hedge Maze, Fishing Village |
| Mystic Manor | 12 | mansion, basement, passages and rooftops |
| Hedge Maze | 4 | Grave Plot, Cave-in, Tomb 1, Tomb 3 |
| Smuggler's Cove | 3 | Coast, Fishy Clues, Shiver |

Inside each group the keys are sorted alphabetically by their nameplate label.
The grouping itself is derived from the apworld rather than typed out: each key
gates a connection, and the connection's region gives the map. Hedge Maze and Fishing
Village gate doors leading *out of* the Mystery Machine, so they are Hub keys
even though the rooms they open are elsewhere.

Every key used to share one image (`images/items/key.png`), which is why the grid
needed a three-letter header over each one. Each key now has its own **nameplate**
instead - a 96x32 image with the key icon and its name - so the headers are gone.
That is also shorter: a group header costs 36px of height, so dropping 15 of them
saves more than the wider plates cost.

```sh
python3 tools/make_key_nameplates.py   # placeholder art
python3 tools/make_key_grids.py        # layouts/keys.json + keyrings.json
```

The plates in `images/items/keys/` and `images/items/keyrings/` are hand-drawn.
To install a new set, name each PNG after its plate label ("GRAVE PLOT.png") and:

```sh
python3 tools/install_nameplates.py "path/to/Name Plates"
```

That renames them to the item codes the pack expects. By default the same plate
serves a key and its keyring, since the two are never on screen together; pass
`--keyrings-from` for a separate set.

Plates are 3:1 - 192x64 source, rendered at 96x32 and 72x24 in the compact
portrait layout. `make_key_grids.py --check-art` fails if one is missing, and
`make_key_nameplates.py` now refuses to overwrite existing art without `--force`,
so re-running it cannot clobber the real plates.

Keys are consumables - you may need up to 5 of one - and PopTracker draws that
count at the item's bottom edge. The plates are inked corner to corner, so the
count needs `overlay_background` to stay readable; it sits bottom-right, away
from the key art on the left. Change `overlay_align` in `items/items.json` to
`left` or `center` to move it. If you want it clear of the lettering entirely,
leave ~20px of empty space in the bottom-right of the plate art.

Keyrings are `toggle` items rather than consumables capped at 1. The logic only
ever asks `aphas("<x>ring")` with no count, so a consumable just painted a
pointless "1" over the art. The autotracker dispatches on the item's real type,
so it takes the toggle branch and still sets them.

In the portrait layout the groups are split across two columns - Hub, Hedge Maze
and Smuggler's Cove in one, Mystic Manor in the other, since it has as many keys
as the other three combined. That takes the block from 312x336 to 616x186.

Both that grid and the portrait band are `dock` nodes with their children docked
`left`, not horizontal arrays. An HBox always stretches its **last** child to
soak up leftover width (see `HBox::relayout`), which dragged the group's title
bar across the whole window. Inside a dock, children that are all docked keep
their own width. Worth remembering for any future row that should not stretch.

To change a label or move a key between groups, edit `LABELS` in
`tools/make_key_nameplates.py` and re-run `make_key_grids.py` - the alphabetical
ordering follows the label, so renaming a plate reorders it automatically.

## Keys and Keyrings swap automatically

`include_keys` in the apworld is 0 off / 1 keys / 2 keyrings, so only one of the
two grids is ever worth showing. PopTracker has no visibility rules for layout
widgets (only locations have those), but `Tracker:AddLayouts()` **replaces** any
layout name it redefines and fires `onLayoutChanged`, which makes the view rebuild
while keeping the active map tab.

So `active_keys_grid` is defined in three small files and `scripts/keysanity_layout.lua`
loads whichever matches the setting:

| setting  | file                          | result                       |
|----------|-------------------------------|------------------------------|
| off      | `layouts/active_none.json`    | empty array, takes no space  |
| keys     | `layouts/active_keys.json`    | pastes `keys_grid`           |
| keyrings | `layouts/active_keyrings.json`| pastes `keyrings_grid`       |

It swaps only when the value actually changes, since rebuilding the view is not
free. The per-map group headers live *inside* `keys_grid`, not around it, so with
keysanity off the section disappears completely rather than leaving an empty
"Keys" header behind.

## Warps are map-only

The warp grid was removed in 1.1.0 - warps are already shown on the map as
trapezoids. The warp **items** are still registered in `items.json`, so
`$warpMissing|<code>`, the logic and autotracking all work exactly as before;
only the clickable grid is gone. The trade-off is that a player not using
autotracking can no longer toggle a warp by hand.

`layouts/warps.json` is still in the repo but is not loaded. To bring the grid
back, re-add its `Tracker:AddLayouts` line in `scripts/init.lua` and put a
"Warps" group back into `layouts/tracker.json`.

## The All Maps variant

All four areas at once, as a 3x2 grid:

```
  (blank)   Mystic Manor   (blank)
Hedge Maze      Hub      Smuggler's Cove
```

This is a **pack variant**, not a tab: pick "All Maps" instead of "Individual
Maps" when loading the pack. `manifest.json` declares both, and `scripts/init.lua`
checks `Tracker.ActiveVariantUID` to load `layouts/tabs_allmaps.json` (one
untabbed combined map) in place of `layouts/tabs.json`.

`images/maps/all.png` and all 279 of its pins are generated, so the combined view
always tracks exactly the same checks as the per-area tabs:

```sh
python3 tools/make_all_maps.py
```

Re-run after moving any pin or changing a map image; it is idempotent. Each panel
sits in a cell sized to the largest map so nothing is distorted.

Pin sizes are in **map pixels**, so at the default `--pin-scale 1.0` a pin covers
the same part of the art as it does on the single-area maps and the combined view
looks like the individual ones, just smaller. Raising it keeps pins the same size
in screen pixels instead, which looks enormous on the shrunken panels - that was
the mistake in the first cut of this feature.

`--scale 0.75` trades some sharpness for a smaller image if pack size matters.

## Boss rewards show their boss token

The three boss reward checks used the generic flower chest icon, which made them
indistinguishable from any other reward. They now use the matching monster token
art the pack already ships, in the same closed/open pair every other token
location uses:

| check | art |
|-------|-----|
| Black Knight Reward | `images/items/tokens/knight.png` |
| Redbeard Reward | `images/items/tokens/redbeard.png` |
| Reward from Green Ghost | `images/items/tokens/green.png` |

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
