"""Determinism lint for a lockstep simulation folder: `godot-agent check <project> --sim-lint scripts/sim`.

A lockstep game runs the same simulation on every client and compares state hashes, so the
simulation code must give bit-identical results on every CPU, OS and in the web build. This lint
fails on tokens that break that: floats, transcendental functions, engine RNGs, float vector
types, the scene tree, wall-clock time and frame deltas, engine and OS state, float rounding,
Variant hashing, Node types, address-ordered sorts and instance ids, deferred work, and
iterating a dictionary (any name the folder declares as one, or a function typed -> Dictionary).

Strings and comments are not linted. A line may allow a token with a reason:

    var turn_ms := 50  # sim-lint: allow delta the name of a field in the wire format

The token after `allow` is a rule name (`float-literal`, `sqrt`, `node-type`, ...) or the exact
text the lint reported (`0.5`, `Node2D`); several may be given separated by commas. An allow
without a reason does not count.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path


NODE_TYPES_FILE = Path(__file__).with_name("sim_lint_nodes.txt")
ALLOW_RE = re.compile(r"sim-lint:\s*allow\s+(\S+)(?:\s+(\S.*))?")


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern
    why: str


def _call(names: str) -> str:
    """A call of a global function (not a method of some object): `sqrt(x)`, not `turns.log(x)`."""
    return rf"(?<![\w.])(?:{names})\s*\("


_FLOAT_TYPES = "use integers in the units the rules name (mm, um, ticks, centi-hp)"
_TRANSCENDENTAL = "transcendental float math can differ between CPUs and the web build; use integer math (isqrt)"
_RNG = "engine RNG; draw from the simulation's own seeded RNG, whose state is part of the checkpoint"
_VECTOR = "float vector or transform type; use ints, Vector2i or Vector3i"
_TREE = "the simulation has no scene tree; keep entities in arrays ordered by id"
_TIME = "wall-clock or frame time; the simulation counts ticks"
_ORDER = "a dictionary's order is its insertion history; iterate an array of ids in a fixed order"
_SYNC = "the simulation steps synchronously; deferred work runs on frame time"

# A dictionary declared in code: `var d := {`, `var d: Dictionary`, a parameter `d: Dictionary`, or a
# function typed `-> Dictionary`. Iterating one (`for k in d:`, `for k in content.rules(...):`) is the
# dict-iteration rule.
_DICT_DECL = re.compile(r"\b(?:var|const)\s+(\w+)\s*(?::?=\s*\{|:\s*Dictionary\b)|(\w+)\s*:\s*Dictionary\b"
                        r"|\bfunc\s+(\w+)\s*\([^)]*\)\s*->\s*Dictionary\b")
_FOR_IN = re.compile(r"\bfor\s+\w+(?:\s*:\s*\w+)?\s+in\s+([\w.]+)\s*[:(]")


def _rules() -> list[Rule]:
    rules = [
        Rule("float", re.compile(r"\bfloat\b|\bPackedFloat(?:32|64)Array\b"), "float type; " + _FLOAT_TYPES),
        Rule("float-literal", re.compile(
            r"(?<![\w.])(?:\d[\d_]*\.[\d_]*(?:[eE][+-]?\d[\d_]*)?|\.\d[\d_]*(?:[eE][+-]?\d[\d_]*)?"
            r"|\d[\d_]*[eE][+-]?\d[\d_]*)"), "float literal; " + _FLOAT_TYPES),
        Rule("float-constant", re.compile(r"\b(?:PI|TAU|INF|NAN)\b"), "float constant; " + _FLOAT_TYPES),
        Rule("float-function", re.compile(_call(
            "absf|clampf|minf|maxf|signf|wrapf|snappedf|fmod|fposmod|floorf|ceilf|roundf|is_equal_approx"
            "|is_zero_approx|is_finite|is_inf|is_nan|smoothstep|move_toward|remap|deg_to_rad|rad_to_deg|ease"
            "|cubic_interpolate|bezier_interpolate|linear_to_db|db_to_linear")), "float function; " + _FLOAT_TYPES),
    ]
    for name in ("sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sinh", "cosh", "tanh", "asinh",
                 "acosh", "atanh", "sqrt", "pow", "exp", "log"):
        rules.append(Rule(name, re.compile(_call(name)), _TRANSCENDENTAL))
    rules += [
        Rule("lerp", re.compile(r"\b\w*lerp\w*\b"), "interpolation is float math; views interpolate, the simulation does not"),
        Rule("randf", re.compile(r"\brandf\w*\b"), _RNG),
        Rule("randi", re.compile(r"\brandi\w*\b"), _RNG),
        Rule("randomize", re.compile(r"\brandomize\b|\bRandomNumberGenerator\b|(?<![\w.])seed\s*\("), _RNG),
        Rule("shuffle", re.compile(r"\bshuffle\b"), _RNG),
        Rule("pick_random", re.compile(r"\bpick_random\b"), _RNG),
    ]
    for name in ("Vector2", "Vector3", "Vector4", "Quaternion", "Basis", "Rect2", "AABB", "Plane", "Projection"):
        rules.append(Rule(name, re.compile(rf"\b{name}\b"), _VECTOR))
    rules += [
        Rule("Transform", re.compile(r"\bTransform(?:2D|3D)?\b"), _VECTOR),
        Rule("look_at", re.compile(r"\blook_at\w*\b"), _VECTOR),
        Rule("get_tree", re.compile(r"\bget_tree\b|\bSceneTree\b"), _TREE),
        Rule("get_nodes_in_group", re.compile(r"\bget_nodes_in_group\b|\bget_first_node_in_group\b"), _TREE),
        Rule("Time.", re.compile(r"\bTime\s*\."), _TIME),
        Rule("OS.", re.compile(r"\bOS\s*\."), "the machine's clocks, environment and platform differ per client"),
        Rule("Engine.", re.compile(r"\bEngine\s*\."),
             "engine state (time scale, frame count, meta) is not part of the match; pass in what the rules need"),
        Rule("delta", re.compile(r"\bdelta\b"), _TIME),
        Rule("time_scale", re.compile(r"\btime_scale\b"), _TIME),
        Rule("hash(", re.compile(r"\bhash\s*\("),
             "Variant hashing is not specified to be stable; hash state with HashingContext"),
        Rule("rounding", re.compile(_call("ceil|floor|round|ceili|floori|roundi|snapped|snappedi")),
             "rounds a float; " + _FLOAT_TYPES),
        Rule("to_float", re.compile(r"(?<=\.)to_float(?=\s*\()"), "float conversion; " + _FLOAT_TYPES),
        Rule("length()", re.compile(r"(?<=\.)length(?=\s*\(\s*\))"),
             "a vector's length is a float; compare squared integer distances"),
        Rule("keys()", re.compile(r"(?<=\.)(?:keys|values)(?=\s*\()"), _ORDER),
        Rule("sort()", re.compile(r"(?<=\.)sort(?=\s*\(\s*\))"),
             "Array.sort orders objects by address; sort ints only and say so in an allow comment"),
        Rule("sort_custom", re.compile(r"\bsort_custom\b"),
             "the sort is not stable; break ties by id and say so in an allow comment"),
        Rule("get_instance_id", re.compile(r"\bget_instance_id\b"), "instance ids differ between runs and machines"),
        Rule("await", re.compile(r"\bawait\b"), _SYNC),
        Rule("call_deferred", re.compile(r"\bcall_deferred\b|\bset_deferred\b"), _SYNC),
        Rule("node-type", re.compile(r"\b(?:" + "|".join(sorted(node_types(), key=len, reverse=True)) + r")\b"),
             "Node type; the simulation is RefCounted records with no nodes"),
    ]
    return rules


def node_types() -> list[str]:
    words: list[str] = []
    for line in NODE_TYPES_FILE.read_text().splitlines():
        if not line.startswith("#"):
            words += line.split()
    return words


_RULES: list[Rule] | None = None


def rules() -> list[Rule]:
    global _RULES
    if _RULES is None:
        _RULES = _rules()
    return _RULES


# ── Reading GDScript ──

def split_source(source: str) -> tuple[list[str], list[str]]:
    """Per line: the code with every string blanked to spaces (columns kept), and the comment text."""
    code = list(source)
    comments: list[list[str]] = [[]]
    line = 0
    i, n = 0, len(source)
    while i < n:
        c = source[i]
        if c == "\n":
            line += 1
            comments.append([])
            i += 1
        elif c == "#":
            end = source.find("\n", i)
            end = n if end < 0 else end
            comments[line].append(source[i:end])
            for j in range(i, end):
                code[j] = " "
            i = end
        elif c in "\"'":
            quote = c * 3 if source.startswith(c * 3, i) else c
            j = i + len(quote)
            while j < n:
                if source[j] == "\\":
                    j += 2
                    continue
                if source.startswith(quote, j):
                    j += len(quote)
                    break
                if source[j] == "\n" and len(quote) == 1:
                    break
                j += 1
            j = min(j, n)
            for k in range(i, j):
                if source[k] == "\n":
                    line += 1
                    comments.append([])
                else:
                    code[k] = " "
            i = j
        else:
            i += 1
    return "".join(code).split("\n"), [" ".join(parts) for parts in comments]


def _allows(comment: str) -> tuple[set[str], list[str]]:
    """Tokens a comment allows, and tokens it names without a reason."""
    allowed: set[str] = set()
    missing: list[str] = []
    for match in ALLOW_RE.finditer(comment):
        tokens = [t for t in match.group(1).split(",") if t]
        if match.group(2):
            allowed.update(tokens)
        else:
            missing.extend(tokens)
    return allowed, missing


def dictionary_names(source: str) -> set[str]:
    """Names one file's code declares as dictionaries (see _DICT_DECL)."""
    code = "\n".join(split_source(source)[0])
    return {name for match in _DICT_DECL.finditer(code) for name in match.groups() if name}


def lint_source(source: str, dictionaries: set[str] | frozenset[str] = frozenset()) -> tuple[list[dict], int]:
    """Problems in one file's source, and how many hits an allow comment excused. `dictionaries` names
    the dictionaries other files of the folder declare, so iterating one of theirs is caught too."""
    problems: list[dict] = []
    excused = 0
    code_lines, comment_lines = split_source(source)
    known = set(dictionaries) | dictionary_names(source)
    source_lines = source.split("\n")
    for number, (code, comment) in enumerate(zip(code_lines, comment_lines), 1):
        allowed, missing = _allows(comment)
        for token in missing:
            problems.append({"line": number, "column": 1, "rule": "allow", "token": token,
                             "why": f"'sim-lint: allow {token}' needs a reason after the token",
                             "source": source_lines[number - 1].strip()})
        if not code.strip():
            continue
        for rule in rules():
            for match in rule.pattern.finditer(code):
                text = match.group(0).rstrip("( \t")
                if rule.name in allowed or text in allowed:
                    excused += 1
                    continue
                problems.append({"line": number, "column": match.start() + 1, "rule": rule.name, "token": text,
                                 "why": rule.why, "source": source_lines[number - 1].strip()})
        loop = _FOR_IN.search(code)
        if loop and (loop.group(1) in known or loop.group(1).rsplit(".", 1)[-1] in known):
            if "dict-iteration" in allowed or loop.group(1) in allowed:
                excused += 1
            else:
                problems.append({"line": number, "column": loop.start(1) + 1, "rule": "dict-iteration",
                                 "token": loop.group(1), "why": _ORDER, "source": source_lines[number - 1].strip()})
    problems.sort(key=lambda p: (p["line"], p["column"]))
    return problems, excused


def _resolve(project: Path, raw: str) -> Path:
    if raw.startswith("res://"):
        return project / raw[len("res://"):]
    path = Path(raw).expanduser()
    return path if path.is_absolute() else project / path


def lint_paths(project: str | os.PathLike, paths: list[str]) -> dict:
    """Lints every .gd file in the given folders or files (relative to the project, or res://)."""
    project = Path(project).expanduser().resolve()
    files: list[Path] = []
    problems: list[dict] = []
    for raw in paths:
        path = _resolve(project, raw)
        if path.is_dir():
            files += sorted(p for p in path.rglob("*.gd")
                            if not any(part.startswith(".") for part in p.relative_to(path).parts))
        elif path.is_file():
            files.append(path)
        else:
            problems.append({"file": raw, "line": 0, "column": 0, "rule": "path", "token": raw,
                             "why": "no such file or folder", "source": ""})
    excused = 0
    sources = {file: file.read_text(encoding="utf-8", errors="replace") for file in dict.fromkeys(files)}
    dictionaries = set().union(*(dictionary_names(text) for text in sources.values()))
    for file, text in sources.items():
        found, allowed = lint_source(text, dictionaries)
        excused += allowed
        rel = os.path.relpath(file, project)
        problems += [{"file": rel, **p} for p in found]
    return {"paths": paths, "files": len(files), "problems": problems, "allowed": excused}


def format_lint(report: dict, limit: int = 40) -> list[str]:
    lines = [f"  sim-lint: {report['files']} files in {', '.join(report['paths'])} · "
             f"{len(report['problems'])} forbidden tokens · {report['allowed']} allowed by comment"]
    for p in report["problems"][:limit]:
        lines.append(f"    {p['file']}:{p['line']}:{p['column']}  {p['token']}  ({p['rule']}: {p['why']})")
    if len(report["problems"]) > limit:
        lines.append(f"    ... {len(report['problems']) - limit} more")
    return lines


def _print_node_types() -> None:
    from . import api
    classes = api.ensure_index()["classes"]

    def is_node(name: str) -> bool:
        while name:
            if name == "Node":
                return True
            name = classes.get(name, {}).get("inherits", "")
        return False

    import textwrap
    tag = api.ensure_index()["tag"]
    print(f"# Every Node class of Godot {tag} (and Node itself), from the class reference: the sim lint's")
    print("# node-type rule. Regenerate with: python3 -m godot_agent.simlint --node-types > godot_agent/sim_lint_nodes.txt")
    print(textwrap.fill(" ".join(sorted(c for c in classes if is_node(c))), 110))


if __name__ == "__main__":
    if sys.argv[1:] != ["--node-types"]:
        sys.exit("usage: python3 -m godot_agent.simlint --node-types  (lint with: godot-agent check <project> --sim-lint <folder>)")
    _print_node_types()
