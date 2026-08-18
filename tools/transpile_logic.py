#!/usr/bin/env python3
"""
Regenerate scripts/logic/logic.lua from a Scooby-Doo! Night of 100 Frights apworld.

The PopTracker pack cannot read Archipelago's Python logic at runtime, so the
apworld's rules are transpiled into Lua ahead of time. When the apworld ships new
logic, drop the new .apworld in and re-run this:

    python3 tools/transpile_logic.py path/to/no100f.apworld

What it reads from the apworld:
    Regions.py    - the region graph (exit_table)
    Rules.py      - entrance/location access rules, including option-gated ones
    Locations.py  - location ids, used for the reachable-snack counters
    names/*.py    - region / connection / location / item name constants

What it reads from the pack (these are pack-side facts, not apworld facts):
    tools/region_keys.json  - the "$no100f|N" region ids used by locations.json
    locations/locations.json - which room owns each "Scooby Snacks" section
    items/items.json        - the tracker codes the Lua is allowed to reference

Rule composition mirrors Archipelago's own set_rules(): every rule is recorded
with the option guard that was active when it was applied, and the emitted Lua is
the AND over all `(not guard) or rule` pairs. That way one Lua file covers every
YAML combination instead of only the seed it was generated from.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from typing import Any

# --------------------------------------------------------------------------
# option atoms
# --------------------------------------------------------------------------

# apworld option name -> tracker code that is set when that option is enabled
OPTION_CODES = {
    "include_monster_tokens": "op_tokensanity_on",
    "include_keys": "op_keysanity_on",
    "include_warpgates": "op_warps_on",
    "include_snacks": "op_snacksanity_on",
    "advanced_logic": "op_advanced_on",
    "expert_logic": "op_expert_on",
    "creepy_early": "op_creepy_on",
}

# ItemNames location-type constant -> the option that enables that location type
LOC_TYPE_OPTION = {
    "Upgrades": None,  # always on
    "victory": None,  # always on
    "MonsterTokens": "include_monster_tokens",
    "Keys": "include_keys",
    "Warps": "include_warpgates",
    "Snacks": "include_snacks",
}

# rule table name -> the option that enables the whole table
RULE_TABLE_OPTION = {
    "upgrade_rules": None,
    "monster_token_rules": "include_monster_tokens",
    "key_rules": "include_keys",
    "warpgate_rules": "include_warpgates",
    "snack_rules": "include_snacks",
}

# Items whose tracker representation is not a plain "name with spaces removed"
# code. Progressive Jump is two separate tracker codes rather than a count.
PROGRESSIVE_JUMP = {1: "springs", 2: "umbrella"}


class Unsupported(Exception):
    """Raised when the apworld uses a construct this transpiler does not model."""


# --------------------------------------------------------------------------
# boolean expression tree
#
# Nodes are tuples so they are hashable and cheap to compare:
#   ("true",) ("false",)
#   ("has", code, count)
#   ("snack", n)
#   ("reach", region_display_name)
#   ("reach_entrance", connection_attr)
#   ("reach_loc", location_attr)
#   ("and", a, b, ...) ("or", a, b, ...) ("not", a)
# --------------------------------------------------------------------------

TRUE = ("true",)
FALSE = ("false",)


def mk_and(*parts):
    flat = []
    for p in parts:
        if p == TRUE:
            continue
        if p == FALSE:
            return FALSE
        if p[0] == "and":
            flat.extend(p[1:])
        else:
            flat.append(p)
    seen, uniq = set(), []
    for p in flat:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    if not uniq:
        return TRUE
    if len(uniq) == 1:
        return uniq[0]
    return ("and", *uniq)


def mk_or(*parts):
    flat = []
    for p in parts:
        if p == FALSE:
            continue
        if p == TRUE:
            return TRUE
        if p[0] == "or":
            flat.extend(p[1:])
        else:
            flat.append(p)
    seen, uniq = set(), []
    for p in flat:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    if not uniq:
        return FALSE
    if len(uniq) == 1:
        return uniq[0]
    return ("or", *uniq)


def mk_not(a):
    if a == TRUE:
        return FALSE
    if a == FALSE:
        return TRUE
    if a[0] == "not":
        return a[1]
    return ("not", a)


def to_lua(node) -> str:
    kind = node[0]
    if kind == "true":
        return "true"
    if kind == "false":
        return "false"
    if kind == "has":
        _, code, count = node
        if count == 1:
            return f'aphas("{code}")'
        return f'aphas("{code}", {count})'
    if kind == "snack":
        return f"snackcount({node[1]})"
    if kind == "reach":
        return f'reach({lua_str(node[1])})'
    if kind == "reach_entrance":
        return f'reachEntrance("{node[1]}")'
    if kind == "reach_loc":
        return f'reachLoc("{node[1]}")'
    if kind == "not":
        return f"(not {to_lua(node[1])})"
    if kind == "and":
        return "(" + " and ".join(to_lua(p) for p in node[1:]) + ")"
    if kind == "or":
        return "(" + " or ".join(to_lua(p) for p in node[1:]) + ")"
    raise Unsupported(f"cannot emit node {node!r}")


def lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def collect_codes(node, out: set):
    kind = node[0]
    if kind == "has":
        out.add(node[1])
    elif kind == "snack":
        out.add("ap_snack_logic")
    elif kind in ("and", "or"):
        for p in node[1:]:
            collect_codes(p, out)
    elif kind == "not":
        collect_codes(node[1], out)


# --------------------------------------------------------------------------
# apworld loading
# --------------------------------------------------------------------------


def load_apworld(path: str, workdir: str) -> str:
    """Return the directory holding the world package (the one with Rules.py)."""
    if os.path.isdir(path):
        root = path
    else:
        root = os.path.join(workdir, "apworld")
        os.makedirs(root, exist_ok=True)
        with zipfile.ZipFile(path) as zf:
            zf.extractall(root)
    for dirpath, _dirnames, filenames in os.walk(root):
        if "Rules.py" in filenames and "Regions.py" in filenames:
            return dirpath
    raise Unsupported(f"no world package (Rules.py + Regions.py) found in {path}")


def import_names(world_dir: str, workdir: str):
    """Import the apworld's names/ package standalone (no Archipelago needed)."""
    pkg_parent = os.path.join(workdir, "namespkg")
    if os.path.exists(pkg_parent):
        shutil.rmtree(pkg_parent)
    os.makedirs(pkg_parent)
    shutil.copytree(os.path.join(world_dir, "names"), os.path.join(pkg_parent, "names"))
    init = os.path.join(pkg_parent, "names", "__init__.py")
    if not os.path.exists(init):
        open(init, "w").close()
    sys.path.insert(0, pkg_parent)
    try:
        import importlib

        for mod in ("LevelNames", "RegionNames", "ConnectionNames", "ItemNames", "LocationNames"):
            importlib.import_module(f"names.{mod}")
        import names  # noqa: F401

        return {
            m: importlib.import_module(f"names.{m}")
            for m in ("LevelNames", "RegionNames", "ConnectionNames", "ItemNames", "LocationNames")
        }
    finally:
        sys.path.remove(pkg_parent)


# --------------------------------------------------------------------------
# pack-side facts
# --------------------------------------------------------------------------


def load_jsonc(path: str):
    """Parse PopTracker's JSON dialect (// comments, trailing commas)."""
    src = open(path, encoding="utf-8").read()
    out, i, n, instr = [], 0, len(src), False
    while i < n:
        c = src[i]
        if instr:
            out.append(c)
            if c == "\\":
                out.append(src[i + 1])
                i += 2
                continue
            if c == '"':
                instr = False
            i += 1
            continue
        if c == '"':
            instr = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return json.loads(re.sub(r",(\s*[\]}])", r"\1", "".join(out)))


def pack_item_codes(pack_dir: str) -> set:
    codes = set()
    for item in load_jsonc(os.path.join(pack_dir, "items", "items.json")):
        fields = [item.get("codes", "")]
        for stage in item.get("stages", []) or []:
            fields.append(stage.get("codes", ""))
        for f in fields:
            for c in str(f).split(","):
                c = c.strip()
                if c:
                    codes.add(c)
    return codes


def snack_section_keys(pack_dir: str) -> dict:
    """room name -> $no100f region id guarding that room's 'Scooby Snacks' section."""
    tree = load_jsonc(os.path.join(pack_dir, "locations", "locations.json"))
    result = {}

    def rule_keys(rules) -> set:
        keys = set()
        for r in rules or []:
            text = r if isinstance(r, str) else " ".join(str(x) for x in r)
            for m in re.finditer(r"\$no100f\|(\d+)", text):
                keys.add(int(m.group(1)))
        return keys

    def walk(nodes):
        for nd in nodes:
            name = nd.get("name", "")
            room_keys = rule_keys(nd.get("access_rules"))
            for sec in nd.get("sections", []) or []:
                if sec.get("name") != "Scooby Snacks":
                    continue
                keys = rule_keys(sec.get("access_rules")) or room_keys
                if len(keys) == 1:
                    result[name] = next(iter(keys))
                elif keys:
                    raise Unsupported(
                        f"room {name!r} snack section has ambiguous region keys {sorted(keys)}"
                    )
            walk(nd.get("children", []) or [])

    walk(tree)
    return result


# --------------------------------------------------------------------------
# name resolution
# --------------------------------------------------------------------------


class Names:
    def __init__(self, mods):
        self.mods = mods
        # value -> attribute name, per module (attribute names are the Lua keys)
        self.attr_of = {}
        for mod_name, mod in mods.items():
            rev = {}
            for attr in dir(mod):
                if attr.startswith("_"):
                    continue
                val = getattr(mod, attr)
                if isinstance(val, str):
                    rev.setdefault(val, attr)
            self.attr_of[mod_name] = rev

    def value(self, mod_name: str, attr: str) -> str:
        mod = self.mods.get(mod_name)
        if mod is None or not hasattr(mod, attr):
            raise Unsupported(f"unknown name {mod_name}.{attr}")
        return getattr(mod, attr)


def item_code(attr: str, ap_name: str, valid_codes: set) -> str:
    """Map an ItemNames constant to its tracker code."""
    code = ap_name.lower().replace(" ", "")
    if code not in valid_codes:
        raise Unsupported(
            f"ItemNames.{attr} ({ap_name!r}) has no tracker code {code!r} in items/items.json"
        )
    return code


# --------------------------------------------------------------------------
# lambda body -> expression tree
# --------------------------------------------------------------------------


class RuleCompiler:
    def __init__(self, names: Names, valid_codes: set):
        self.names = names
        self.valid_codes = valid_codes
        self.quirks: list[str] = []

    def qualified(self, node) -> tuple | None:
        """Return (module, attr) for a Module.attr expression."""
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return node.value.id, node.attr
        return None

    def const_int(self, node) -> int | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        return None

    def compile(self, node) -> Any:
        if isinstance(node, ast.BoolOp):
            parts = [self.compile(v) for v in node.values]
            return mk_and(*parts) if isinstance(node.op, ast.And) else mk_or(*parts)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return mk_not(self.compile(node.operand))
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            return TRUE if node.value else FALSE
        if isinstance(node, ast.Call):
            return self.compile_call(node)
        # A bare literal in a boolean position is almost always a typo upstream --
        # e.g. `(ItemNames.PoundPower, player, 1)` with the `state.has(` left off.
        # Python evaluates it by truthiness, so Archipelago's own logic does too;
        # mirror that exactly rather than guessing at the intent, and report it.
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            self.quirks.append(
                f"literal {'tuple' if isinstance(node, ast.Tuple) else 'collection'} used as a "
                f"condition (line {getattr(node, 'lineno', '?')}) -- looks like a missing "
                f"state.has(); AP treats it as always-{'true' if node.elts else 'false'}"
            )
            return TRUE if node.elts else FALSE
        if isinstance(node, ast.Constant):
            self.quirks.append(
                f"constant {node.value!r} used as a condition (line {getattr(node, 'lineno', '?')})"
            )
            return TRUE if node.value else FALSE
        raise Unsupported(f"rule expression {ast.dump(node)[:160]}")

    def compile_call(self, node: ast.Call):
        func = node.func
        if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
            raise Unsupported(f"rule call {ast.dump(node)[:160]}")
        if func.value.id != "state":
            raise Unsupported(f"rule calls non-state object {func.value.id}")

        if func.attr == "has":
            return self.compile_has(node)
        if func.attr == "can_reach":
            return self.compile_can_reach(node)
        if func.attr in ("has_all", "has_any"):
            names_node = node.args[0]
            if not isinstance(names_node, (ast.List, ast.Tuple, ast.Set)):
                raise Unsupported("state.has_all/has_any with non-literal collection")
            parts = []
            for elt in names_node.elts:
                q = self.qualified(elt)
                if q is None or q[0] != "ItemNames":
                    raise Unsupported("state.has_all/has_any with non-ItemNames entry")
                parts.append(self.item_atom(q[1], 1))
            return mk_and(*parts) if func.attr == "has_all" else mk_or(*parts)
        raise Unsupported(f"state.{func.attr} is not supported")

    def item_atom(self, attr: str, count: int):
        ap_name = self.names.value("ItemNames", attr)
        if attr == "ProgressiveJump":
            if count not in PROGRESSIVE_JUMP:
                raise Unsupported(f"ProgressiveJump count {count} has no tracker code")
            return ("has", PROGRESSIVE_JUMP[count], 1)
        if attr == "Snack":
            return ("snack", count)
        return ("has", item_code(attr, ap_name, self.valid_codes), count)

    def compile_has(self, node: ast.Call):
        if len(node.args) < 2:
            raise Unsupported("state.has with too few args")
        q = self.qualified(node.args[0])
        if q is None or q[0] != "ItemNames":
            raise Unsupported(f"state.has first arg {ast.dump(node.args[0])[:120]}")
        count = 1
        if len(node.args) >= 3:
            c = self.const_int(node.args[2])
            if c is None:
                # e.g. options.token_count -- only appears in goal rules, which are
                # handled by the hand-written goal functions in the Lua prelude.
                raise Unsupported("state.has with a non-constant count")
            count = c
        return self.item_atom(q[1], count)

    def compile_can_reach(self, node: ast.Call):
        q = self.qualified(node.args[0])
        if q is None:
            raise Unsupported(f"can_reach first arg {ast.dump(node.args[0])[:120]}")
        mod, attr = q
        kind = None
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            kind = node.args[1].value
        for kw in node.keywords:
            if kw.arg == "resolution_hint" and isinstance(kw.value, ast.Constant):
                kind = kw.value.value
        if kind is None:
            kind = {"RegionNames": "Region", "ConnectionNames": "Entrance", "LocationNames": "Location"}.get(mod)
        if kind == "Region":
            return ("reach", self.names.value(mod, attr))
        if kind == "Entrance":
            return ("reach_entrance", attr)
        if kind == "Location":
            return ("reach_loc", attr)
        raise Unsupported(f"can_reach resolution hint {kind!r}")


# --------------------------------------------------------------------------
# Rules.py extraction
# --------------------------------------------------------------------------


class RulesExtractor:
    def __init__(self, tree: ast.Module, names: Names, compiler: RuleCompiler):
        self.tree = tree
        self.names = names
        self.compiler = compiler
        self.entrance_rules: dict[str, list] = {}
        self.location_rules: dict[str, list] = {}
        self.skipped: list[str] = []
        self.tables = {}
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                t = stmt.targets[0]
                if isinstance(t, ast.Name) and t.id in RULE_TABLE_OPTION:
                    self.tables[t.id] = stmt.value

    # -- guards ---------------------------------------------------------
    def option_guard(self, name: str):
        code = OPTION_CODES.get(name)
        if code is None:
            raise Unsupported(f"option {name} has no tracker code")
        return ("has", code, 1)

    def loc_type_guard(self, attr: str):
        opt = LOC_TYPE_OPTION.get(attr)
        if attr not in LOC_TYPE_OPTION:
            raise Unsupported(f"unknown location type ItemNames.{attr}")
        return TRUE if opt is None else self.option_guard(opt)

    def eval_guard(self, node):
        """Compile an `if` test in set_rules into a guard expression."""
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return mk_not(self.eval_guard(node.operand))
        if isinstance(node, ast.BoolOp):
            parts = [self.eval_guard(v) for v in node.values]
            return mk_and(*parts) if isinstance(node.op, ast.And) else mk_or(*parts)
        # options.<name>.value
        if isinstance(node, ast.Attribute) and node.attr == "value":
            inner = node.value
            if isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name) and inner.value.id == "options":
                return self.option_guard(inner.attr)
        # options.<name>
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "options":
            return self.option_guard(node.attr)
        # goal == N  -> handled by the hand-written goal functions
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "goal":
            return ("goal",)
        # ItemNames.X in / not in allowed_loc_types
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, op, right = node.left, node.ops[0], node.comparators[0]
            if isinstance(right, ast.Name) and right.id == "allowed_loc_types":
                if isinstance(left, ast.Attribute) and isinstance(left.value, ast.Name) and left.value.id == "ItemNames":
                    g = self.loc_type_guard(left.attr)
                    if isinstance(op, ast.In):
                        return g
                    if isinstance(op, ast.NotIn):
                        return mk_not(g)
        raise Unsupported(f"guard {ast.dump(node)[:160]}")

    # -- rule tables ----------------------------------------------------
    def lambda_body(self, node):
        """Unwrap `lambda player: lambda state: <expr>` or `lambda state: <expr>`."""
        if isinstance(node, ast.Tuple):
            node = node.elts[0]
        if isinstance(node, ast.Lambda):
            inner = node.body
            if isinstance(inner, ast.Lambda):
                return inner.body
            return inner
        raise Unsupported(f"rule value {ast.dump(node)[:120]}")

    def forced_override(self, node) -> bool:
        """`(rule, True)` means set_rule (replace) instead of add_rule (and)."""
        if isinstance(node, ast.Tuple) and len(node.elts) > 1:
            flag = node.elts[1]
            return isinstance(flag, ast.Constant) and bool(flag.value)
        return False

    def add_entrance(self, attr: str, guard, expr, override=False):
        self.entrance_rules.setdefault(attr, []).append((guard, expr, override))

    def add_location(self, attr: str, guard, expr, override=False):
        self.location_rules.setdefault(attr, []).append((guard, expr, override))

    def expand_table(self, table_name: str, guard):
        node = self.tables.get(table_name)
        if node is None:
            raise Unsupported(f"rule table {table_name} not found")
        if not isinstance(node, (ast.List, ast.Tuple)) or len(node.elts) < 2:
            raise Unsupported(f"rule table {table_name} has an unexpected shape")
        conn_dict, loc_dict = node.elts[0], node.elts[1]

        for key, value in zip(conn_dict.keys, conn_dict.values):
            q = self.compiler.qualified(key)
            if q is None or q[0] != "ConnectionNames":
                raise Unsupported(f"{table_name} connection key {ast.dump(key)[:100]}")
            try:
                expr = self.compiler.compile(self.lambda_body(value))
            except Unsupported as e:
                self.skipped.append(f"{table_name} entrance {q[1]}: {e}")
                continue
            self.add_entrance(q[1], guard, expr, self.forced_override(value))

        for lt_key, lt_val in zip(loc_dict.keys, loc_dict.values):
            q = self.compiler.qualified(lt_key)
            if q is None or q[0] != "ItemNames":
                raise Unsupported(f"{table_name} loc-type key {ast.dump(lt_key)[:100]}")
            lt_guard = mk_and(guard, self.loc_type_guard(q[1]))
            for key, value in zip(lt_val.keys, lt_val.values):
                lq = self.compiler.qualified(key)
                if lq is None or lq[0] != "LocationNames":
                    raise Unsupported(f"{table_name} location key {ast.dump(key)[:100]}")
                try:
                    expr = self.compiler.compile(self.lambda_body(value))
                except Unsupported as e:
                    self.skipped.append(f"{table_name} location {lq[1]}: {e}")
                    continue
                self.add_location(lq[1], lt_guard, expr, self.forced_override(value))

    # -- set_rules ------------------------------------------------------
    def run(self):
        fn = None
        for stmt in self.tree.body:
            if isinstance(stmt, ast.FunctionDef) and stmt.name == "set_rules":
                fn = stmt
        if fn is None:
            raise Unsupported("set_rules() not found in Rules.py")
        self.walk(fn.body, TRUE)

    def walk(self, body, guard):
        for stmt in body:
            if isinstance(stmt, ast.If):
                try:
                    g = self.eval_guard(stmt.test)
                except Unsupported as e:
                    self.skipped.append(f"branch: {e}")
                    continue
                if g == ("goal",):
                    # Credits/goal rules live in the hand-written goal functions.
                    continue
                self.walk(stmt.body, mk_and(guard, g))
                if stmt.orelse:
                    self.walk(stmt.orelse, mk_and(guard, mk_not(g)))
                continue
            if isinstance(stmt, ast.Expr):
                value = stmt.value
                # A stray trailing comma turns `add_rule(...)` into a 1-tuple
                # expression statement. Python still evaluates the call, so the
                # rule is live in Archipelago and must not be dropped here.
                if isinstance(value, ast.Tuple):
                    for elt in value.elts:
                        self.handle_call(elt, guard)
                else:
                    self.handle_call(value, guard)
                continue
            # assignments (allowed_loc_types bookkeeping) and everything else: ignore

    def handle_call(self, node, guard):
        if not isinstance(node, ast.Call):
            return
        fname = node.func.id if isinstance(node.func, ast.Name) else None

        if fname in ("_add_rules", "_set_rules"):
            table = node.args[2]
            if not isinstance(table, ast.Name):
                raise Unsupported("rule table argument is not a name")
            extra = RULE_TABLE_OPTION.get(table.id)
            g = guard if extra is None else mk_and(guard, self.option_guard(extra))
            self.expand_table(table.id, g)
            return

        if fname in ("add_rule", "set_rule"):
            target, rule = node.args[0], node.args[1]
            if not isinstance(target, ast.Call) or not isinstance(target.func, ast.Attribute):
                return
            getter = target.func.attr
            q = self.compiler.qualified(target.args[0])
            if q is None:
                return
            try:
                expr = self.compiler.compile(self.lambda_body(rule))
            except Unsupported as e:
                self.skipped.append(f"{getter} {q[1]}: {e}")
                return
            override = fname == "set_rule"
            if getter == "get_entrance":
                self.add_entrance(q[1], guard, expr, override)
            elif getter == "get_location":
                if q[1] == "Credits":
                    return  # goal rules
                self.add_location(q[1], guard, expr, override)
            return

    # -- composition ----------------------------------------------------
    @staticmethod
    def combine(entries):
        """AND together `(not guard) or rule`, honouring set_rule overrides.

        add_rule ANDs onto whatever came before. set_rule replaces it, so anything
        recorded before an override only applies when the override's guard is off.
        """
        result = TRUE
        for guard, expr, override in entries:
            if override and guard != TRUE:
                result = mk_and(mk_or(guard, result), mk_or(mk_not(guard), expr))
            elif override:
                result = expr
            else:
                result = mk_and(result, mk_or(mk_not(guard), expr))
        return result


# --------------------------------------------------------------------------
# Regions.py / Locations.py extraction
# --------------------------------------------------------------------------


def parse_exit_table(tree: ast.Module, names: Names):
    """region display name -> list of (connection attr, target region name)."""
    node = None
    for stmt in tree.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == "exit_table":
            node = stmt.value
        elif isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "exit_table" for t in stmt.targets
        ):
            node = stmt.value
    if node is None:
        raise Unsupported("exit_table not found in Regions.py")

    table = []
    for key, value in zip(node.keys, node.values):
        if not (isinstance(key, ast.Attribute) and isinstance(key.value, ast.Name) and key.value.id == "RegionNames"):
            raise Unsupported(f"exit_table key {ast.dump(key)[:100]}")
        region = names.value("RegionNames", key.attr)
        conns = []
        for elt in value.elts:
            if not (
                isinstance(elt, ast.Attribute)
                and isinstance(elt.value, ast.Name)
                and elt.value.id == "ConnectionNames"
            ):
                raise Unsupported(f"exit_table entry {ast.dump(elt)[:100]}")
            conn_value = names.value("ConnectionNames", elt.attr)
            if "->" not in conn_value:
                # Menu's "Start Game" is wired up by hand in create_regions() and
                # always leads to the start region, which reach() treats as free.
                continue
            target = conn_value.split("->", 1)[1]
            conns.append((elt.attr, target))
        table.append((region, conns))
    return table


def parse_location_tables(world_dir: str, names: Names):
    """Return {table_name: {LocationNames attr: id}} from Locations.py."""
    src = open(os.path.join(world_dir, "Locations.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    consts: dict[str, int] = {}
    tables: dict[str, dict] = {}

    def eval_int(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.Name) and node.id in consts:
            return consts[node.id]
        if isinstance(node, ast.BinOp):
            left, right = eval_int(node.left), eval_int(node.right)
            if left is None or right is None:
                return None
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
        return None

    for stmt in tree.body:
        target = None
        value = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            target, value = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            target, value = stmt.target.id, stmt.value
        if target is None or value is None:
            continue
        n = eval_int(value)
        if n is not None:
            consts[target] = n
            continue
        if isinstance(value, ast.Dict) and target.endswith("_table"):
            entries = {}
            for k, v in zip(value.keys, value.values):
                # `**other_table` spreads show up as a None key
                if k is None:
                    if isinstance(v, ast.Name) and v.id in tables:
                        entries.update(tables[v.id])
                    continue
                if not (
                    isinstance(k, ast.Attribute)
                    and isinstance(k.value, ast.Name)
                    and k.value.id == "LocationNames"
                ):
                    continue
                entries[k.attr] = eval_int(v)
            tables[target] = entries
    return tables


# --------------------------------------------------------------------------
# emit
# --------------------------------------------------------------------------

PRELUDE = '''
function aphas(code, n) return Tracker:ProviderCountForCode(code) >= (n or 1) end
function tokencount(n) return Tracker:ProviderCountForCode("progressivemonstertoken") >= n end
function snackcount(n)
  -- AP logic counts received "Scooby Snack" items only (not Spare Snacks or Boxes),
  -- matching state.has(ItemNames.Snack, player, n) in the apworld.
  return Tracker:ProviderCountForCode("ap_snack_logic") >= n
end

-- Region reachability is a least fixpoint, exactly like Archipelago's own sweep:
-- start from START and keep relaxing edges until nothing new opens up. Rules that
-- call reach() during the sweep read the partial set, which is what AP does too.
-- (A plain per-query DFS gets this wrong for rules that reference other regions.)
local REACH, DIRTY, COMPUTING = {}, true, false

function invalidateReach() DIRTY = true end

local function computeReach()
  COMPUTING = true
  REACH = { [START] = true }
  local changed, passes = true, 0
  while changed and passes < 64 do
    changed = false
    passes = passes + 1
    for r, exits in pairs(REGION_CONN) do
      if REACH[r] then
        for _, e in ipairs(exits) do
          if not REACH[e.to] then
            local ok = true
            if e.rule then
              local called, value = pcall(e.rule)
              ok = called and value and true or false
            end
            if ok then
              REACH[e.to] = true
              changed = true
            end
          end
        end
      end
    end
  end
  COMPUTING = false
  DIRTY = false
end

function reach(region)
  if COMPUTING then return REACH[region] == true end
  if DIRTY then computeReach() end
  return REACH[region] == true
end

function reachEntrance(cv)
  local e = ENTRANCE[cv]
  if not e then return false end
  if not reach(e.from) then return false end
  if e.rule and not e.rule() then return false end
  return true
end

-- reach a specific AP location: its region must be reachable AND its own rule met
function reachLoc(locname)
  local reg = LOC_REGION[locname]
  if reg and not reach(reg) then return false end
  local lr = LOC_RULE[locname]
  if lr and not lr() then return false end
  return true
end

function no100f(key)
  local reg = RKEY[tonumber(key)]
  if not reg then return 1 end
  return reach(reg) and 1 or 0
end

-- locations.json calls this for per-location rules layered on region access
LOC_RULE_FN = LOC_RULE
function lr(var)
  local f = LOC_RULE[var]
  if f == nil then return 1 end
  return f() and 1 or 0
end

function snackReachable(k, var)
  if reach(RKEY[k]) ~= true then return false end
  if var ~= "" then
    local f = LOC_RULE[var]
    if f and not f() then return false end
  end
  return true
end

function warpMissing(code) return (Tracker:ProviderCountForCode(code) == 0) and 1 or 0 end
'''

GOAL_BLOCK = '''
-- ===== completion goal =====================================================
-- Mirrors the apworld's rules on the Credits location. Driven by the
-- "Completion Goal" setting plus the required boss/token/snack counts, all of
-- which auto-apply from slot_data.
function bossesDone()
  local have = (reachLoc("boots_o008") and 1 or 0) + (reachLoc("umbrella_g009") and 1 or 0) + (reachLoc("gumpower_w028") and 1 or 0)
  local need = Tracker:ProviderCountForCode("goal_boss_count")
  if need < 1 then need = 3 end
  return have >= need
end

function tokensDone()
  local need = Tracker:ProviderCountForCode("goal_token_count")
  if need < 1 then need = 21 end
  if aphas("op_tokensanity_on") then
    return tokencount(need)
  end
  -- tokens are not items: the apworld substitutes reaching every token room
  return TOKEN_ROOMS_REACHABLE()
     and aphas("umbrella") and aphas("helmetpower") and aphas("soapbubblepower")
     and aphas("plungerspower") and aphas("bootspowerup")
end

function snacksDone()
  local need = Tracker:ProviderCountForCode("goal_snack_count")
  if need < 1 then need = 850 end
  return snackcount(need)
end

function goalMet()
  if not reach(GOAL_REGION) then return 0 end
  local any = false
  if aphas("goal_bosses") then any = true; if not bossesDone() then return 0 end end
  if aphas("goal_tokens") then any = true; if not tokensDone() then return 0 end end
  if aphas("goal_snacks") then any = true; if not snacksDone() then return 0 end end
  if not any then
    -- vanilla
    if not (aphas("groundpoundpower") and aphas("helmetpower")) then return 0 end
  end
  return 1
end
'''


def rule_field(expr) -> str:
    return "nil" if expr == TRUE else f"function() return {to_lua(expr)} end"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("apworld", help="path to no100f.apworld (or an extracted directory)")
    ap.add_argument("--pack", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."),
                    help="pack root (default: repo root)")
    ap.add_argument("--out", default=None, help="output path (default: <pack>/scripts/logic/logic.lua)")
    ap.add_argument("--dump-json", default=None, help="also write the intermediate rule model as JSON")
    args = ap.parse_args()

    pack_dir = os.path.abspath(args.pack)
    out_path = args.out or os.path.join(pack_dir, "scripts", "logic", "logic.lua")

    workdir = tempfile.mkdtemp(prefix="no100f-transpile-")
    try:
        world_dir = load_apworld(args.apworld, workdir)
        mods = import_names(world_dir, workdir)
        names = Names(mods)
        valid_codes = pack_item_codes(pack_dir)
        compiler = RuleCompiler(names, valid_codes)

        rules_tree = ast.parse(open(os.path.join(world_dir, "Rules.py"), encoding="utf-8").read())
        regions_tree = ast.parse(open(os.path.join(world_dir, "Regions.py"), encoding="utf-8").read())

        extractor = RulesExtractor(rules_tree, names, compiler)
        extractor.run()

        exit_table = parse_exit_table(regions_tree, names)
        loc_tables = parse_location_tables(world_dir, names)

        # ---- region keys used by locations.json --------------------------
        region_keys = {int(k): v for k, v in json.load(
            open(os.path.join(pack_dir, "tools", "region_keys.json"), encoding="utf-8")
        ).items()}
        known_regions = {r for r, _ in exit_table}
        for key, region in sorted(region_keys.items()):
            if region not in known_regions:
                print(f"  warning: region_keys.json[{key}] = {region!r} is not a region in this apworld",
                      file=sys.stderr)

        start_region = names.value("RegionNames", "hub1")
        goal_region = names.value("RegionNames", "s004")

        # ---- entrance + region tables ------------------------------------
        entrance_expr = {a: RulesExtractor.combine(e) for a, e in extractor.entrance_rules.items()}
        location_expr = {a: RulesExtractor.combine(e) for a, e in extractor.location_rules.items()}

        # location attr -> region display name (for reachLoc)
        loc_region: dict[str, str] = {}
        location_table = loc_tables.get("location_table", {})
        loc_value_to_attr = names.attr_of["LocationNames"]
        for region, _ in exit_table:
            prefix = f"{region}:"
            for attr in location_table:
                val = names.value("LocationNames", attr)
                if prefix in val:
                    loc_region.setdefault(attr, region)

        lines = []
        w = lines.append
        w("-- AUTO-GENERATED from the NO100F apworld by tools/transpile_logic.py.")
        w("-- Do not hand-edit: re-run the script against the new apworld instead.")
        w("-- Mirrors Rules.py so tracker logic matches Archipelago for every YAML combo.")
        w("")
        w(f"local START = {lua_str(start_region)}")
        w(f"local GOAL_REGION = {lua_str(goal_region)}")
        w("")

        w("REGION_CONN = {")
        for region, conns in exit_table:
            parts = []
            for attr, target in conns:
                expr = entrance_expr.get(attr, TRUE)
                parts.append(f"{{to={lua_str(target)}, rule={rule_field(expr)}}}")
            w(f"  [{lua_str(region)}] = {{{', '.join(parts)}}},")
        # regions that are only ever a target still need an entry
        targets = {t for _, conns in exit_table for _, t in conns}
        for t in sorted(targets - known_regions):
            w(f"  [{lua_str(t)}] = {{}},")
        w("}")
        w("")

        w("ENTRANCE = {")
        for region, conns in exit_table:
            for attr, _target in conns:
                expr = entrance_expr.get(attr, TRUE)
                w(f'  ["{attr}"] = {{from={lua_str(region)}, rule={rule_field(expr)}}},')
        w("}")
        w("")

        w("LOC_RULE = {")
        for attr in sorted(location_expr):
            expr = location_expr[attr]
            if expr == TRUE:
                continue
            w(f'  ["{attr}"] = function() return {to_lua(expr)} end,')
        w("}")
        w("")

        w("LOC_REGION = {")
        for attr in sorted(location_expr):
            reg = loc_region.get(attr)
            if reg:
                w(f'  ["{attr}"] = {lua_str(reg)},')
        w("}")
        w("")

        # ---- codes that should invalidate the reachability cache ----------
        codes = set()
        for expr in list(entrance_expr.values()) + list(location_expr.values()):
            collect_codes(expr, codes)
        codes |= {"ap_snack_logic", "progressivemonstertoken"}
        codes |= set(OPTION_CODES.values())
        w("-- recompute reachability only when a logic-relevant item changes")
        w("LOGIC_CODES = { " + ", ".join(lua_str(c) for c in sorted(codes)) + " }")
        w("")

        w("RKEY = {")
        for key in sorted(region_keys):
            w(f"  [{key}] = {lua_str(region_keys[key])},")
        w("}")
        w("")

        w(PRELUDE.strip())
        w("")

        # ---- snack counters ----------------------------------------------
        snack_table = loc_tables.get("snack_location_table", {})
        room_key = snack_section_keys(pack_dir)
        # AP region -> pack room name (rooms are keyed by their locations.json name)
        key_region = region_keys
        by_room: dict[str, list] = {}
        for attr, loc_id in snack_table.items():
            val = names.value("LocationNames", attr)
            region = val.split(":", 1)[0].strip() if ":" in val else None
            if region is None:
                continue
            var = attr if attr in location_expr else ""
            by_room.setdefault(region, []).append((loc_id, var))

        w("-- per-region snack data: {id, region_key, loc_rule_var} (reachable-snack counters)")
        w("SNACK_BY_REGION = {")
        unmatched = []
        for room, key in sorted(room_key.items(), key=lambda kv: kv[1]):
            region = key_region.get(key)
            entries = by_room.get(region, [])
            if not entries:
                unmatched.append((room, key, region))
                continue
            body = ", ".join(f'{{{i},{key},"{v}"}}' for i, v in sorted(entries))
            w(f"  [{lua_str(room)}] = {{{body}}},")
        w("}")
        w("")
        w(GOAL_BLOCK.strip())
        w("")

        # ---- token rooms for the non-tokensanity goal ---------------------
        token_table = loc_tables.get("monstertoken_location_table", {})
        token_regions = []
        for attr in token_table:
            val = names.value("LocationNames", attr)
            if ":" in val:
                r = val.split(":", 1)[0].strip()
                if r not in token_regions:
                    token_regions.append(r)
        w("function TOKEN_ROOMS_REACHABLE()")
        w("  return " + ("\n     and ".join(f"reach({lua_str(r)})" for r in token_regions) or "true"))
        w("end")
        w("")

        w("if ScriptHost and ScriptHost.AddWatchForCode then")
        w("  for _, c in ipairs(LOGIC_CODES) do")
        w('    ScriptHost:AddWatchForCode("no100f_logic_"..c, c, function() invalidateReach() end)')
        w("  end")
        w("end")
        w("")

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        print(f"wrote {out_path}")
        print(f"  regions        {len(exit_table)}")
        print(f"  entrances      {sum(len(c) for _, c in exit_table)}")
        print(f"  entrance rules {sum(1 for e in entrance_expr.values() if e != TRUE)}")
        print(f"  location rules {sum(1 for e in location_expr.values() if e != TRUE)}")
        print(f"  snack rooms    {len(room_key) - len(unmatched)}")
        print(f"  logic codes    {len(codes)}")
        if unmatched:
            print(f"  warning: {len(unmatched)} pack rooms had no snacks in this apworld:", file=sys.stderr)
            for room, key, region in unmatched[:10]:
                print(f"    {room!r} (key {key} -> region {region!r})", file=sys.stderr)
        if extractor.skipped:
            print(f"  note: {len(extractor.skipped)} rules skipped (goal rules are handled separately):")
            for s in extractor.skipped[:15]:
                print(f"    {s}")
        if compiler.quirks:
            print(f"  note: {len(compiler.quirks)} apworld quirk(s) mirrored as-is "
                  f"(worth reporting upstream):")
            for q in dict.fromkeys(compiler.quirks):
                print(f"    {q}")

        if args.dump_json:
            model = {
                "start": start_region,
                "entrances": {a: to_lua(e) for a, e in sorted(entrance_expr.items())},
                "locations": {a: to_lua(e) for a, e in sorted(location_expr.items())},
                "loc_region": loc_region,
            }
            with open(args.dump_json, "w", encoding="utf-8") as f:
                json.dump(model, f, indent=1, ensure_ascii=False)
            print(f"wrote {args.dump_json}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
