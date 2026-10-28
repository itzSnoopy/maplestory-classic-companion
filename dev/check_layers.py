"""Layer check: which module imports which, against the target layers in .claude/handoff/09-restructure-plan.md.

    python dev/check_layers.py                report only (exit 0)
    python dev/check_layers.py --strict       exit 1 if there is any violation
    python dev/check_layers.py --max N        exit 1 if there are more than N violations (the count may only go down)
    python dev/check_layers.py --json PATH    also write the violation list as JSON

Scans every tracked .py file (not dev/, packaging/, relay/, build/, dist/) with the ast module, top-level
and function-local imports alike. Modules that are not project modules (stdlib, third party) are ignored.
A project file missing from MAP is listed as "unmapped": put it in a layer. Stdlib only; touches nothing.
"""
import argparse
import ast
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --- Modules sit in folders (base/ launcher/ engine/ gamedata/ hub/ components/<name>/): layer_of() maps them by
# --- path. MAP is only for the leftovers at the repo root.
MAP = {"companion": "launcher"}     # root entry shim (PyInstaller script, start_*.cmd)

# importer layer -> layers it may import (same layer / same component is always allowed).
# "base" (base/: paths, lean, version, perf, internet, contract) is allowed for everyone and imports nothing.
RULES = {
    "base": set(),
    "launcher": {"hub", "component", "engine", "gamedata", "base"},
    "hub": {"gamedata", "base"},
    "engine": {"base"},
    "gamedata": {"base"},
    "component": {"hub", "gamedata", "base"},
}
SKIP_DIRS = {"dev", "packaging", "relay", "build", "dist", "__pycache__"}
FOLDER_LAYERS = {"base", "launcher", "engine", "gamedata", "hub"}


def module_of(path):
    """Repo-relative path -> dotted module name."""
    parts = list(path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def layer_of(mod):
    """Dotted module name -> layer ('component:x', 'hub', ...) or None if unknown/not ours."""
    top = mod.split(".")[0]
    if top in FOLDER_LAYERS:
        return top
    if top == "components":
        parts = mod.split(".")
        return "component:" + parts[1] if len(parts) > 1 else "launcher"   # components/__init__ = the registry (composition, like launcher)
    return MAP.get(top)


def kind(layer):
    return "component" if layer.startswith("component:") else layer


def allowed(src, dst):
    if src == dst:
        return True
    return kind(dst) in RULES[kind(src)]


def tracked_files():
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True).stdout
    files = [Path(p) for p in out.split() if p]
    return [p for p in files if not (set(p.parts[:-1]) & SKIP_DIRS)]


def imports_of(path):
    """Yield (line, dotted module) for every import in the file."""
    try:
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as e:
        print(f"! cannot parse {path}: {e}", file=sys.stderr)
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield node.lineno, a.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module
            for a in node.names:       # 'from sources import wgc' names a submodule
                yield node.lineno, node.module + "." + a.name


def resolve(dotted, known):
    """Longest prefix of the dotted name that is a project module -> (name, layer), else (None, None)."""
    parts = dotted.split(".")
    for n in range(len(parts), 0, -1):
        name = ".".join(parts[:n])
        if name in known or (n == 1 and (name in FOLDER_LAYERS or name == "components")):
            lay = layer_of(name)
            if lay:
                return name, lay
    return None, None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--strict", action="store_true", help="exit 1 on any violation")
    ap.add_argument("--max", type=int, default=None, help="exit 1 if more than N violations")
    ap.add_argument("--json", default=None, help="write violations to this file")
    args = ap.parse_args()

    files = tracked_files()
    known = {module_of(p) for p in files}
    known_tops = {m.split(".")[0] for m in known}
    unmapped, violations, seen = [], [], set()
    for p in files:
        mod = module_of(p)
        src = layer_of(mod)
        if src is None:
            unmapped.append(p.as_posix())
            continue
        for line, dotted in imports_of(p):
            if dotted.split(".")[0] not in known_tops:
                continue
            name, dst = resolve(dotted, known)
            if not dst or not name:
                continue
            if allowed(src, dst):
                continue
            key = (p.as_posix(), line, dst)
            if key in seen:
                continue
            seen.add(key)
            violations.append({"file": p.as_posix(), "line": line, "importer": mod, "importer_layer": src,
                               "target": name, "target_layer": dst})

    by_layer = defaultdict(list)
    for v in violations:
        by_layer[v["importer_layer"]].append(v)
    for lay in sorted(by_layer):
        print(f"== {lay} ({len(by_layer[lay])})")
        for v in sorted(by_layer[lay], key=lambda x: (x["file"], x["line"])):
            print(f"  {v['file']}:{v['line']}  {v['importer']}({v['importer_layer']}) -> {v['target']}({v['target_layer']})")
    print("\n== by rule pair")
    pairs = Counter((v["importer_layer"], v["target_layer"]) for v in violations)
    for (a, b), n in pairs.most_common():
        print(f"  {n:4d}  {a} -> {b}")
    if unmapped:
        print("\n== unmapped (put these in a layer)")
        for u in unmapped:
            print("  " + u)
    print(f"\nTOTAL violations: {len(violations)}  (files scanned: {len(files)}, unmapped: {len(unmapped)})")

    if args.json:
        Path(args.json).write_text(json.dumps(violations, indent=1), encoding="utf-8")
    if (args.strict and violations) or (args.max is not None and len(violations) > args.max):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
