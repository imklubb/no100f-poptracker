#!/usr/bin/env python3
"""
Check that the generated Lua logic agrees with the apworld's real Python rules.

This does not re-read the transpiler's output model. It loads the apworld's own
Rules.py / Regions.py with lightweight Archipelago stubs, actually runs
set_rules(), and computes region reachability the way Archipelago does. Then it
runs scripts/logic/logic.lua under a real Lua interpreter with a mock Tracker and
compares which regions each side says are reachable.

Both sides are driven from the same randomised states: a random YAML option combo
plus a random subset of items. Any disagreement is a transpiler bug.

    python3 tools/verify_logic.py path/to/no100f.apworld [--trials 400]

Requires lua5.4 on PATH.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import types
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.abspath(os.path.join(HERE, ".."))

sys.path.insert(0, HERE)
from transpile_logic import (  # noqa: E402
    OPTION_CODES,
    PROGRESSIVE_JUMP,
    load_apworld,
    load_jsonc,
    pack_item_codes,
)


# --------------------------------------------------------------------------
# Archipelago stubs -- just enough for Regions.py / Rules.py / Locations.py
# --------------------------------------------------------------------------


def install_stubs():
    bc = types.ModuleType("BaseClasses")

    class Region:
        def __init__(self, name, player, multiworld=None):
            self.name = name
            self.player = player
            self.multiworld = multiworld
            self.locations = []
            self.exits = []

    class Entrance:
        def __init__(self, player, name, parent=None):
            self.name = name
            self.player = player
            self.parent_region = parent
            self.connected_region = None
            self.access_rule = lambda state: True

        def connect(self, region):
            self.connected_region = region

    class Location:
        def __init__(self, player, name, address=None, parent=None):
            self.name = name
            self.player = player
            self.address = address
            self.parent_region = parent
            self.access_rule = lambda state: True

    class CollectionState:
        pass

    class MultiWorld:
        pass

    class Item:
        pass

    class ItemClassification:
        progression = 1
        useful = 2
        filler = 3
        trap = 4
        progression_skip_balancing = 5

    class Tutorial:
        def __init__(self, *a, **k):
            pass

    bc.Region = Region
    bc.Entrance = Entrance
    bc.Location = Location
    bc.CollectionState = CollectionState
    bc.MultiWorld = MultiWorld
    bc.Item = Item
    bc.ItemClassification = ItemClassification
    bc.Tutorial = Tutorial
    sys.modules["BaseClasses"] = bc

    gr = types.ModuleType("worlds.generic.Rules")

    def set_rule(spot, rule):
        spot.access_rule = rule

    def add_rule(spot, rule, combine="and"):
        old = spot.access_rule
        if combine == "or":
            spot.access_rule = lambda state: old(state) or rule(state)
        else:
            spot.access_rule = lambda state: old(state) and rule(state)

    gr.set_rule = set_rule
    gr.add_rule = add_rule
    gr.CollectionRule = object
    gr.forbid_item = lambda *a, **k: None

    worlds = types.ModuleType("worlds")
    worlds.__path__ = []
    generic = types.ModuleType("worlds.generic")
    generic.__path__ = []
    sys.modules.setdefault("worlds", worlds)
    sys.modules["worlds.generic"] = generic
    sys.modules["worlds.generic.Rules"] = gr

    opt = types.ModuleType("Options")

    class _Opt:
        default = 0

        def __init__(self, value=None):
            self.value = self.default if value is None else value

    for nm in ("Toggle", "DeathLink", "Range", "Choice", "StartInventoryPool", "DefaultOnToggle",
               "PerGameCommonOptions", "OptionSet", "TextChoice", "FreeText", "NamedRange"):
        setattr(opt, nm, type(nm, (_Opt,), {}))
    opt.DefaultOnToggle.default = 1
    sys.modules["Options"] = opt

    utils = types.ModuleType("Utils")
    utils.open_filename = lambda *a, **k: None
    sys.modules["Utils"] = utils
    return bc


def import_world(world_dir: str, workdir: str):
    """Import the apworld package under a synthetic name with stubs in place."""
    import importlib

    pkg_root = os.path.join(workdir, "pkg")
    if os.path.exists(pkg_root):
        shutil.rmtree(pkg_root)
    os.makedirs(pkg_root)
    dest = os.path.join(pkg_root, "no100fworld")
    shutil.copytree(world_dir, dest)
    # Drop __init__.py's Archipelago-heavy imports; we only need the data modules.
    with open(os.path.join(dest, "__init__.py"), "w", encoding="utf-8") as f:
        f.write("from .Options import NO100FOptions\n")
    sys.path.insert(0, pkg_root)
    mods = {}
    for name in ("Options", "names.RegionNames", "names.ConnectionNames", "names.ItemNames",
                 "names.LocationNames", "Locations", "Regions", "Rules"):
        mods[name] = importlib.import_module(f"no100fworld.{name}")
    return mods


# --------------------------------------------------------------------------
# reference model
# --------------------------------------------------------------------------


class Opt:
    def __init__(self, value):
        self.value = value

    def __int__(self):
        return int(self.value)

    def __index__(self):
        return int(self.value)

    def __eq__(self, other):
        return self.value == other

    def __hash__(self):
        return hash(self.value)


class Options:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, Opt(v))


class RefWorld:
    """Builds regions + rules from the real apworld and answers can_reach()."""

    def __init__(self, mods, options):
        self.mods = mods
        self.R = mods["names.RegionNames"]
        self.C = mods["names.ConnectionNames"]
        self.I = mods["names.ItemNames"]
        self.L = mods["names.LocationNames"]
        self.options = options
        self.regions = {}
        self.entrances = {}
        self.locations = {}
        self.player = 1
        self._build(mods["Regions"], mods["Rules"])

    def _build(self, Regions, Rules):
        import BaseClasses as bc

        exit_table = Regions.exit_table
        for name in exit_table:
            self.regions[name] = bc.Region(name, self.player, self)
        for name, exits in exit_table.items():
            for cv in exits:
                e = bc.Entrance(self.player, cv, self.regions[name])
                self.entrances[cv] = e
                self.regions[name].exits.append(e)
                if "->" in cv:
                    target = cv.split("->", 1)[1]
                    if target not in self.regions:
                        self.regions[target] = bc.Region(target, self.player, self)
                    e.connect(self.regions[target])
        start = self.C.start_game
        if start in self.entrances:
            self.entrances[start].connect(self.regions[self.R.hub1])

        # every location that any rule might target
        for table_name in ("location_table",):
            for locname in getattr(self.mods["Locations"], table_name, {}):
                self.locations[locname] = bc.Location(self.player, locname)
        for locname in (self.L.Credits,):
            self.locations.setdefault(locname, bc.Location(self.player, locname))

        Rules.set_rules(self, self.options, self.player)

    # -- the MultiWorld surface Rules.py uses --------------------------
    def get_entrance(self, name, player):
        if name not in self.entrances:
            import BaseClasses as bc

            self.entrances[name] = bc.Entrance(player, name)
        return self.entrances[name]

    def get_location(self, name, player):
        if name not in self.locations:
            import BaseClasses as bc

            self.locations[name] = bc.Location(player, name)
        return self.locations[name]

    def get_region(self, name, player):
        return self.regions[name]

    @property
    def completion_condition(self):
        return {}

    @completion_condition.setter
    def completion_condition(self, v):
        pass

    # -- reachability ---------------------------------------------------
    def reachable(self, state) -> set:
        reach = {self.R.hub1}
        for _ in range(64):
            changed = False
            for name, region in self.regions.items():
                if name not in reach:
                    continue
                for e in region.exits:
                    tgt = e.connected_region
                    if tgt is None or tgt.name in reach:
                        continue
                    state._partial = reach
                    try:
                        ok = bool(e.access_rule(state))
                    except Exception:
                        ok = False
                    if ok:
                        reach.add(tgt.name)
                        changed = True
            if not changed:
                break
        return reach


class RefState:
    def __init__(self, world: RefWorld, items: dict):
        self.world = world
        self.items = items
        self._partial = set()

    def has(self, item, player, count=1):
        return self.items.get(item, 0) >= count

    def has_all(self, names, player):
        return all(self.has(n, player) for n in names)

    def has_any(self, names, player):
        return any(self.has(n, player) for n in names)

    def count(self, item, player):
        return self.items.get(item, 0)

    def can_reach(self, spot, resolution_hint=None, player=None):
        if resolution_hint == "Region":
            return spot in self._partial
        if resolution_hint == "Entrance":
            e = self.world.entrances.get(spot)
            if e is None or e.parent_region is None:
                return False
            if e.parent_region.name not in self._partial:
                return False
            try:
                return bool(e.access_rule(self))
            except Exception:
                return False
        if resolution_hint == "Location":
            loc = self.world.locations.get(spot)
            if loc is None:
                return False
            region = self.world.loc_region.get(spot)
            if region is not None and region not in self._partial:
                return False
            try:
                return bool(loc.access_rule(self))
            except Exception:
                return False
        return spot in self._partial


# --------------------------------------------------------------------------
# lua side
# --------------------------------------------------------------------------

LUA_HARNESS = r"""
local codes = {}
do
  local f = assert(io.open(STATE_FILE, "r"))
  local body = f:read("a"); f:close()
  for k, v in body:gmatch('"([^"]+)"%s*:%s*(%-?%d+)') do codes[k] = tonumber(v) end
end

AccessibilityLevel = { None = 0, Partial = 1, Inspect = 3, SequenceBreak = 5, Normal = 6, Cleared = 7 }
Tracker = {}
function Tracker:ProviderCountForCode(code) return codes[code] or 0 end
function Tracker:FindObjectForCode(code) return nil end
ScriptHost = nil
PopVersion = "0.31.0"

dofile(LOGIC_FILE)

local out = {}
for _, name in ipairs(REGIONS) do
  out[#out+1] = string.format('R%q:%s', name, reach(name) and "true" or "false")
end
for _, name in ipairs(LOCS) do
  out[#out+1] = string.format('L%q:%s', name, reachLoc(name) and "true" or "false")
end
io.write("{", table.concat(out, ","), "}")
"""


def run_lua(logic_file: str, regions: list, locs: list, states: list, workdir: str) -> list:
    """Evaluate reach()/reachLoc() for each state. Returns list of dicts."""
    regions_lua = ("REGIONS = {" + ",".join(lua_quote(r) for r in regions) + "}\n"
                   + "LOCS = {" + ",".join(lua_quote(r) for r in locs) + "}\n")
    results = []
    harness = os.path.join(workdir, "harness.lua")
    for idx, codes in enumerate(states):
        state_file = os.path.join(workdir, f"state{idx}.json")
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(codes, f, separators=(",", ":"))
        with open(harness, "w", encoding="utf-8") as f:
            f.write(f"STATE_FILE = {lua_quote(state_file)}\n")
            f.write(f"LOGIC_FILE = {lua_quote(logic_file)}\n")
            f.write(regions_lua)
            f.write(LUA_HARNESS)
        proc = subprocess.run(["lua5.4", harness], capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit(f"lua failed on trial {idx}:\n{proc.stderr}")
        body = proc.stdout.strip()
        got = {}
        import re

        for m in re.finditer(r'([RL])"((?:[^"\\]|\\.)*)":(true|false)', body):
            key = m.group(1) + m.group(2).encode().decode("unicode_escape")
            got[key] = m.group(3) == "true"
        results.append(got)
    return results


def lua_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --------------------------------------------------------------------------


def build_state(rng, mods, valid_codes):
    """Pick a random option combo + item subset; return (ap_items, tracker_codes)."""
    I = mods["names.ItemNames"]
    opts = {
        "include_monster_tokens": rng.randint(0, 1),
        "include_keys": rng.choice([0, 1, 1, 2]),
        "include_warpgates": rng.randint(0, 1),
        "include_snacks": rng.randint(0, 1),
        "advanced_logic": rng.randint(0, 1),
        "expert_logic": rng.randint(0, 1),
        "creepy_early": rng.randint(0, 1),
        "completion_goal": rng.randint(0, 7),
        "boss_count": 3,
        "token_count": 21,
        "snack_count": 850,
        "speedster": 0,
        "death_link": 0,
        "start_inventory_from_pool": 0,
    }

    upgrades = [I.GumPower, I.SoapPower, I.PoundPower, I.HelmetPower, I.ShockwavePower,
                I.BootsPower, I.PlungerPower, I.ShovelPower]
    items = {}
    for u in upgrades:
        if rng.random() < 0.55:
            items[u] = 2 if u == I.ShockwavePower and rng.random() < 0.5 else 1
    jump = rng.choice([0, 0, 1, 2])
    if jump:
        items[I.ProgressiveJump] = jump
    if rng.random() < 0.5:
        items[I.Snack] = rng.choice([0, 25, 150, 200, 400, 550, 850])
    if rng.random() < 0.5:
        items[I.MT_PROGRESSIVE] = rng.randint(0, 21)

    keyish = [n for n in dir(I) if n.endswith("_Key") or n.endswith("_KeyRing")]
    warpish = [n for n in dir(I) if n.endswith("_Warp")]
    for attr in keyish:
        if rng.random() < 0.45:
            items[getattr(I, attr)] = rng.randint(1, 5)
    for attr in warpish:
        if rng.random() < 0.45:
            items[getattr(I, attr)] = 1

    # mirror the AP item state onto tracker codes
    codes = {}

    def put(code, n):
        if code in valid_codes:
            codes[code] = n

    for ap_name, n in items.items():
        if ap_name == I.ProgressiveJump:
            for level, code in PROGRESSIVE_JUMP.items():
                if n >= level:
                    put(code, 1)
            continue
        if ap_name == I.Snack:
            put("ap_snack_logic", n)
            continue
        put(ap_name.lower().replace(" ", ""), n)

    codes[OPTION_CODES["include_monster_tokens"]] = 1 if opts["include_monster_tokens"] else 0
    codes[OPTION_CODES["include_keys"]] = 1 if opts["include_keys"] else 0
    codes[OPTION_CODES["include_warpgates"]] = 1 if opts["include_warpgates"] else 0
    codes[OPTION_CODES["include_snacks"]] = 1 if opts["include_snacks"] else 0
    codes[OPTION_CODES["advanced_logic"]] = opts["advanced_logic"]
    codes[OPTION_CODES["expert_logic"]] = opts["expert_logic"]
    codes[OPTION_CODES["creepy_early"]] = opts["creepy_early"]
    return opts, items, codes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("apworld")
    ap.add_argument("--logic", default=os.path.join(PACK, "scripts", "logic", "logic.lua"))
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args()

    if shutil.which("lua5.4") is None:
        raise SystemExit("lua5.4 not found on PATH")

    workdir = tempfile.mkdtemp(prefix="no100f-verify-")
    try:
        world_dir = load_apworld(args.apworld, workdir)
        install_stubs()
        mods = import_world(world_dir, workdir)
        valid_codes = pack_item_codes(PACK)
        L = mods["names.LocationNames"]

        # compare exactly the locations the generated Lua carries a rule for
        logic_src = open(args.logic, encoding="utf-8").read()
        import re as _re

        block = logic_src.split("LOC_RULE = {", 1)[1].split("\n}", 1)[0]
        loc_names = _re.findall(r'\["([^"]+)"\]', block)
        loc_value = {a: getattr(L, a) for a in loc_names if hasattr(L, a)}
        missing = [a for a in loc_names if a not in loc_value]
        if missing:
            print(f"  warning: {len(missing)} LOC_RULE names are not in LocationNames: {missing[:5]}")
        loc_names = [a for a in loc_names if a in loc_value]
        print(f"comparing {len(loc_names)} location rules as well as region access")

        rng = random.Random(args.seed)
        trials = [build_state(rng, mods, valid_codes) for _ in range(args.trials)]

        # reference side
        print(f"running {args.trials} trials against the apworld's real rules...")
        ref_results = []
        regions = None
        for opts, items, _codes in trials:
            world = RefWorld(mods, Options(**opts))
            # location -> region, for can_reach(..., "Location")
            world.loc_region = {}
            for rname in world.regions:
                prefix = f"{rname}:"
                for lname in world.locations:
                    if prefix in lname:
                        world.loc_region.setdefault(lname, rname)
            if regions is None:
                regions = sorted(world.regions)
            state = RefState(world, items)
            reach = world.reachable(state)
            state._partial = reach
            row = {"R" + r: True for r in reach}
            for attr in loc_names:
                lname = loc_value.get(attr)
                loc = world.locations.get(lname) if lname else None
                if loc is None:
                    continue
                region = world.loc_region.get(lname)
                ok = True
                if region is not None and region not in reach:
                    ok = False
                else:
                    try:
                        ok = bool(loc.access_rule(state))
                    except Exception:
                        ok = False
                row["L" + attr] = ok
            ref_results.append(row)

        # lua side
        print("running the same trials through logic.lua under lua5.4...")
        lua_results = run_lua(args.logic, regions, loc_names, [c for _o, _i, c in trials], workdir)

        # compare
        keys = ["R" + r for r in regions] + ["L" + n for n in loc_names]
        mismatches = []
        for idx, (ref, got) in enumerate(zip(ref_results, lua_results)):
            for key in keys:
                a = ref.get(key, False)
                b = got.get(key, False)
                if a != b:
                    mismatches.append((idx, key, a, b))

        total = len(trials) * len(keys)
        print(f"\ncompared {total} (trial, region/location) pairs")
        if not mismatches:
            print("PASS - the Lua logic matches Archipelago on every trial")
            return 0

        print(f"FAIL - {len(mismatches)} mismatches ({len(mismatches)/total:.2%})")
        by_region = {}
        for idx, region, a, b in mismatches:
            by_region.setdefault(region, []).append((idx, a, b))
        for region, rows in sorted(by_region.items(), key=lambda kv: -len(kv[1]))[:15]:
            idx, a, b = rows[0]
            print(f"  {region!r}: {len(rows)} trials (e.g. trial {idx}: AP={a} lua={b})")
        worst = sorted(by_region, key=lambda r: -len(by_region[r]))[0]
        idx = by_region[worst][0][0]
        opts, items, codes = trials[idx]
        print(f"\n  first failing state (trial {idx}, region {worst!r}):")
        print("    options:", {k: v for k, v in opts.items() if v})
        print("    items:  ", {k: v for k, v in sorted(items.items())})
        return 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
