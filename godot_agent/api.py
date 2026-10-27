"""Offline, version-pinned Godot class reference.

Signatures and descriptions come from the upstream doc XML at the git tag that
matches the installed Godot binary, so lookups never mix in Godot 3 or a
different 4.x minor. Without network access it falls back to `godot --doctool`,
which has exact signatures but no descriptions.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

from .env import CACHE_DIR, GodotAgentError, find_godot, godot_version, version_tag

REPO = "https://github.com/godotengine/godot.git"
DOC_PATTERNS = ["/doc/classes/", "/modules/*/doc_classes/", "/platform/*/doc_classes/"]


# ── Building ──

def ensure_index(rebuild: bool = False) -> dict:
    godot = find_godot()
    version = godot_version(godot)
    tag = version_tag(version)
    root = CACHE_DIR / "api" / tag
    index_path = root / "index.json"
    if index_path.exists() and not rebuild:
        return json.loads(index_path.read_text())
    root.mkdir(parents=True, exist_ok=True)
    xml_files = _fetch_upstream_docs(tag, root / "src")
    has_descriptions = bool(xml_files)
    if not xml_files:
        xml_files = _doctool_dump(godot, root / "dump")
    classes = {}
    for path in xml_files:
        if parsed := _parse_class(path):
            classes[parsed["name"]] = parsed
    index = {"version": version, "tag": tag, "has_descriptions": has_descriptions, "classes": classes}
    index_path.write_text(json.dumps(index))
    return index


def _fetch_upstream_docs(tag: str, dest: Path) -> list[Path]:
    git = shutil.which("git")
    if not git:
        return []
    if not (dest / ".git").exists():
        shutil.rmtree(dest, ignore_errors=True)
        print(f"godot-agent: fetching Godot {tag} class reference (sparse clone, docs only)...", file=sys.stderr)
        try:
            subprocess.run([git, "clone", "--quiet", "--depth", "1", "--filter=blob:none", "--sparse",
                            "--branch", tag, REPO, str(dest)], check=True, capture_output=True, timeout=600)
            subprocess.run([git, "-C", str(dest), "sparse-checkout", "set", "--no-cone", *DOC_PATTERNS],
                           check=True, capture_output=True, timeout=600)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print(f"godot-agent: could not fetch docs ({exc}); using doctool signatures only", file=sys.stderr)
            shutil.rmtree(dest, ignore_errors=True)
            return []
    return sorted(dest.glob("doc/classes/*.xml")) + sorted(dest.glob("modules/*/doc_classes/*.xml")) \
        + sorted(dest.glob("platform/*/doc_classes/*.xml"))


def _doctool_dump(godot: str, dest: Path) -> list[Path]:
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as cwd:
        subprocess.run([godot, "--headless", "--doctool", str(dest)], cwd=cwd, capture_output=True, timeout=300)
    return sorted(dest.rglob("*.xml"))


def clean(text: str | None) -> str:
    """Godot doc BBCode to plain text with `code` marks."""
    if not text:
        return ""
    text = textwrap.dedent(text).strip()
    text = re.sub(r"\[csharp\].*?\[/csharp\]", "", text, flags=re.S)
    text = re.sub(r"\[/?(codeblocks|gdscript|codeblock)[^\]]*\]", "\n", text)
    text = re.sub(r"\[code\](.*?)\[/code\]", r"`\1`", text, flags=re.S)
    text = re.sub(r"\[(?:method|func) ([^\]]+)\]", r"`\1()`", text)
    text = re.sub(r"\[(?:member|param|signal|constant|enum|annotation|theme_item|operator|constructor) ([^\]]+)\]",
                  r"`\1`", text)
    text = re.sub(r"\[url=[^\]]*\](.*?)\[/url\]", r"\1", text)
    text = re.sub(r"\[/?(b|i|u|kbd|center|lb|rb)\]", "", text)
    text = text.replace("[br]", "\n")
    text = re.sub(r"\[([A-Z@][A-Za-z0-9_]*)\]", r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _deprecated(node: ET.Element) -> str:
    """Marks deprecated APIs so agents steer away from them."""
    if node.get("deprecated") is None:
        return ""
    note = clean(node.get("deprecated"))
    return f" [DEPRECATED{': ' + note if note else ''}]"


def _type(node: ET.Element) -> str:
    return node.get("enum") or node.get("type") or "Variant"


def _params(node: ET.Element) -> str:
    parts = []
    for p in node.findall("param"):
        part = f"{p.get('name')}: {_type(p)}"
        if p.get("default") is not None:
            part += f" = {p.get('default')}"
        parts.append(part)
    return ", ".join(parts)


def _parse_class(path: Path) -> dict | None:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    if root.tag != "class":
        return None
    cls = {"name": root.get("name"), "inherits": root.get("inherits", ""), "deprecated": _deprecated(root),
           "brief": clean(root.findtext("brief_description")),
           "description": clean(root.findtext("description")),
           "methods": [], "properties": [], "signals": [], "constants": []}
    for section in ("constructors", "methods", "annotations"):
        for m in root.findall(f"{section}/*"):
            returns = m.find("return")
            qualifiers = m.get("qualifiers", "")
            sig = f"{m.get('name')}({_params(m)})"
            if returns is not None:
                sig += f" -> {_type(returns)}"
            if qualifiers:
                sig += f" [{qualifiers}]"
            sig += _deprecated(m)
            cls["methods"].append({"name": m.get("name"), "sig": sig, "desc": clean(m.findtext("description"))})
    for m in root.findall("members/member"):
        sig = f"{m.get('name')}: {_type(m)}"
        if m.get("default") is not None:
            sig += f" = {m.get('default')}"
        sig += _deprecated(m)
        cls["properties"].append({"name": m.get("name"), "sig": sig, "desc": clean(m.text)})
    for s in root.findall("signals/signal"):
        cls["signals"].append({"name": s.get("name"), "sig": f"{s.get('name')}({_params(s)})" + _deprecated(s),
                               "desc": clean(s.findtext("description"))})
    for c in root.findall("constants/constant"):
        prefix = f"{c.get('enum')}." if c.get("enum") else ""
        cls["constants"].append({"name": c.get("name"), "sig": f"{prefix}{c.get('name')} = {c.get('value')}" + _deprecated(c),
                                 "desc": clean(c.text)})
    return cls


# ── Querying ──

KINDS = (("methods", "method"), ("properties", "property"), ("signals", "signal"), ("constants", "constant"))


def _first_sentence(text: str, limit: int = 110) -> str:
    sentence = re.split(r"(?<=[.!?])\s", text.replace("\n", " "), maxsplit=1)[0]
    return sentence if len(sentence) <= limit else sentence[: limit - 1] + "…"


def search(query: str, limit: int = 20) -> list[dict]:
    index = ensure_index()
    tokens = [t.lower() for t in re.split(r"[\s]+", query.strip()) if t]
    if not tokens:
        return []
    hits = []

    def score(full: str, short: str, desc: str) -> int:
        total = 0
        full_l, short_l, desc_l = full.lower(), short.lower(), desc.lower()
        for t in tokens:
            if t in (full_l, short_l):
                total += 100
            elif short_l.startswith(t):
                total += 60
            elif t in full_l:
                total += 40
            elif len(t) > 3 and t in desc_l:
                total += 8
            else:
                return 0
        return total

    for cls in index["classes"].values():
        if s := score(cls["name"], cls["name"], cls["brief"]):
            hits.append({"score": s + 5, "kind": "class", "name": cls["name"],
                         "sig": f"class {cls['name']}" + (f" extends {cls['inherits']}" if cls["inherits"] else "")
                         + cls.get("deprecated", ""),
                         "brief": _first_sentence(cls["brief"])})
        for key, kind in KINDS:
            for member in cls[key]:
                full = f"{cls['name']}.{member['name']}"
                if s := score(full, member["name"], member["desc"]):
                    hits.append({"score": s, "kind": kind, "name": full, "sig": f"{cls['name']}.{member['sig']}",
                                 "brief": _first_sentence(member["desc"])})
    hits.sort(key=lambda h: (-h["score"], len(h["name"])))
    return hits[:limit]


def _find_class(index: dict, name: str) -> dict | None:
    if name in index["classes"]:
        return index["classes"][name]
    lowered = {k.lower(): v for k, v in index["classes"].items()}
    return lowered.get(name.lower())


def show(name: str) -> str:
    index = ensure_index()
    class_name, _, member_name = name.partition(".")
    cls = _find_class(index, class_name)
    if cls is None:
        hits = search(class_name, 5)
        suggestion = ", ".join(h["name"] for h in hits)
        raise GodotAgentError(f"No class '{class_name}' in Godot {index['tag']}." +
                              (f" Did you mean: {suggestion}?" if suggestion else ""))
    if member_name:
        found = []
        for key, kind in KINDS:
            for member in cls[key]:
                if member["name"].lower() == member_name.lower():
                    found.append(f"{kind}: {cls['name']}.{member['sig']}\n\n{member['desc'] or '(no description)'}")
        if not found:
            # Inherited members live on a base class: walk up the chain.
            if cls["inherits"]:
                return show(f"{cls['inherits']}.{member_name}")
            raise GodotAgentError(f"No member '{member_name}' on {cls['name']} or its base classes")
        return "\n\n---\n\n".join(found)

    chain = [cls["name"]]
    parent = cls["inherits"]
    while parent and (base := _find_class(index, parent)):
        chain.append(base["name"])
        parent = base["inherits"]
    lines = [f"class {cls['name']}  ({' < '.join(chain)})  · Godot {index['tag']}", ""]
    if cls["brief"]:
        lines += [cls["brief"], ""]
    if cls["description"]:
        description = cls["description"]
        lines += [description if len(description) < 1500 else description[:1500] + " …", ""]
    for key, kind in KINDS:
        if cls[key]:
            lines.append(f"{key}:")
            lines += [f"  {m['sig']}" for m in cls[key]]
            lines.append("")
    lines.append("Inherited members: godot-agent api show <Base>.<member>")
    return "\n".join(lines).rstrip()


def format_hits(hits: list[dict]) -> str:
    if not hits:
        return "no matches"
    return "\n".join(f"{h['kind']:<8} {h['sig']}" + (f"  — {h['brief']}" if h["brief"] else "") for h in hits)
