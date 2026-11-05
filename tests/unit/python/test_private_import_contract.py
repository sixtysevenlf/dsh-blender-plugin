"""Contract test for the addon cross-module private imports.

The addon's polyhaven/polypizza command modules import private helpers from
integrations/*_client.py. That reuse is registered per file in
config/exceptions.json (kind: python-private-import) instead of being renamed
in this change. This pure-ast test keeps the registry honest: every actual
private import must be declared with its real target module, and every
declaration must still exist in the source tree (no stale allowance).

No Blender/bpy import is needed here: the scan never imports the addon.
"""

from __future__ import annotations

import ast
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]
ADDON = ROOT / "addon" / "dsh_blender_bridge"
EXCEPTIONS = ROOT / "config" / "exceptions.json"
PRIVATE_PREFIXES = ("_polyhaven_", "_polypizza_")
KIND = "python-private-import"


def _parse_detail(detail: str) -> tuple[str, set[str]]:
    assert detail.startswith("from "), "detail must start with 'from ': " + detail
    module_part, names_part = detail[len("from ") :].split(" import ", 1)
    names = {name.strip() for name in names_part.split(",") if name.strip()}
    assert names, "detail lists no private names: " + detail
    return module_part.strip(), names


def _registered() -> dict[str, dict[str, str]]:
    registry = json.loads(EXCEPTIONS.read_text(encoding="utf-8"))
    registered: dict[str, dict[str, str]] = {}
    for entry in registry["entries"]:
        if entry["kind"] != KIND:
            continue
        module, names = _parse_detail(entry["detail"])
        per_file = registered.setdefault(entry["path"], {})
        for name in names:
            assert name not in per_file, "duplicate registry name %s in %s" % (name, entry["path"])
            per_file[name] = module
    assert registered, "config/exceptions.json has no %s entries" % KIND
    return registered


def _actual() -> tuple[dict[str, dict[str, str]], int]:
    actual: dict[str, dict[str, str]] = {}
    pairs = 0
    for path in sorted(ADDON.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            names = [alias.name for alias in node.names if alias.name.startswith(PRIVATE_PREFIXES)]
            if not names:
                continue
            module = "." * (node.level or 0) + (node.module or "")
            for name in names:
                actual.setdefault(rel, {})[name] = module
                pairs += 1
    return actual, pairs


def test_every_private_import_is_registered_with_its_real_module():
    registered = _registered()
    actual, pairs = _actual()
    missing = []
    for file, names in actual.items():
        for name, module in sorted(names.items()):
            declared = registered.get(file, {}).get(name)
            if declared is None:
                missing.append("%s imports %s from %s but is not registered" % (file, name, module))
            elif declared != module:
                missing.append(
                    "%s imports %s from %s but registry says %s" % (file, name, module, declared)
                )
    stale = []
    for file, names in registered.items():
        for name in sorted(names):
            if name not in actual.get(file, {}):
                stale.append("%s: registry lists %s but the file no longer imports it" % (file, name))
    assert not missing, "unregistered private imports:\n" + "\n".join(missing)
    assert not stale, "stale private-import exceptions:\n" + "\n".join(stale)
    assert pairs == 41, "expected 41 private-import name pairs, found %d" % pairs
    assert len({name for names in registered.values() for name in names}) == 22
