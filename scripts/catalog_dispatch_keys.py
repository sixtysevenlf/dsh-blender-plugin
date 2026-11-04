
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""catalog_dispatch_keys.py — extract literal Python dispatch keys for the parity test.

Reads config/contracts.json, parses the declared Python files with the stdlib ast
module (no import, no bpy) and prints the key set of each python table as JSON:

    {"tables": {name: {"kind": ..., "file": ..., "keys": [...], "error": null}},
     "errors": ["..."]}

Exit code 0 only when every table resolved to a literal key list. The JS parity
test (tests/unit/js/contract/catalog-dispatch-parity.test.ts) consumes this output
and diffs it against runtime/catalog/data.ts and config/routing.json.

Usage: python scripts/catalog_dispatch_keys.py [repo-root]
"""
import ast
import json
import os
import sys

ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
CONTRACTS = os.path.join(ROOT, "config", "contracts.json")


def dict_keys(node):
    if not isinstance(node, ast.Dict):
        return None
    keys = []
    for key in node.keys:
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            return None
        keys.append(key.value)
    return keys


def find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def assigned_dict(function, var):
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == var:
                    keys = dict_keys(node.value)
                    if keys is not None:
                        return keys
    return None


def returned_dict(function):
    for node in ast.walk(function):
        if isinstance(node, ast.Return):
            keys = dict_keys(node.value)
            if keys is not None:
                return keys
    return None


def register_dict(tree, name):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        label = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if label != "register" or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == name):
            continue
        if len(node.args) >= 3:
            keys = dict_keys(node.args[2])
            if keys is not None:
                return keys
    return None


def api_dict(tree, api):
    for node in ast.walk(tree):
        targets = []
        value = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr == api and isinstance(value, ast.Call):
                for arg in value.args:
                    keys = dict_keys(arg)
                    if keys is not None:
                        return keys
    return None


def function_names(tree, prefix):
    return [
        node.name[len(prefix):]
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith(prefix)
    ]


def resolve(definition):
    path = definition.get("file")
    abs_path = os.path.join(ROOT, path) if isinstance(path, str) else ""
    if not path or not os.path.isfile(abs_path):
        return None, "file not found: %s" % path
    try:
        with open(abs_path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
    except SyntaxError as error:
        return None, "syntax error in %s: %s" % (path, error)
    kind = definition.get("kind")
    if kind == "local-dict":
        function = find_function(tree, str(definition.get("function")))
        if function is None:
            return None, "function %s not found in %s" % (definition.get("function"), path)
        keys = assigned_dict(function, str(definition.get("var")))
        if keys is None:
            return None, "dict %s not found in %s" % (definition.get("var"), definition.get("function"))
        return keys, None
    if kind == "helper-dict":
        function = find_function(tree, str(definition.get("function")))
        if function is None:
            return None, "function %s not found in %s" % (definition.get("function"), path)
        keys = returned_dict(function)
        if keys is None:
            return None, "helper %s does not return a literal dict" % definition.get("function")
        return keys, None
    if kind == "module-dict":
        for node in tree.body:
            targets = []
            value = None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign):
                targets, value = [node.target], node.value
            for target in targets:
                if isinstance(target, ast.Name) and target.id == definition.get("var"):
                    keys = dict_keys(value)
                    if keys is not None:
                        return keys, None
        return None, "module dict %s not found in %s" % (definition.get("var"), path)
    if kind == "register-ops":
        keys = register_dict(tree, str(definition.get("name")))
        if keys is None:
            return None, "register(%s, ...) with a literal ops dict not found in %s" % (
                definition.get("name"),
                path,
            )
        return keys, None
    if kind == "api-dict":
        keys = api_dict(tree, str(definition.get("api")))
        if keys is None:
            return None, "api dict %s not found in %s" % (definition.get("api"), path)
        ignored = set(definition.get("ignore") or [])
        return [key for key in keys if key not in ignored], None
    if kind == "function-names":
        keys = function_names(tree, str(definition.get("prefix")))
        if not keys:
            return None, "no %s* functions found in %s" % (definition.get("prefix"), path)
        return keys, None
    return None, "unknown extraction kind: %r" % kind


def main():
    with open(CONTRACTS, encoding="utf-8") as handle:
        contracts = json.load(handle)
    tables = {}
    errors = []
    for name, definition in contracts.get("pythonTables", {}).items():
        keys, error = resolve(definition)
        tables[name] = {
            "kind": definition.get("kind"),
            "file": definition.get("file"),
            "keys": keys,
            "error": error,
        }
        if error:
            errors.append("%s: %s" % (name, error))
    print(json.dumps({"tables": tables, "errors": errors}, ensure_ascii=False, indent=1))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
