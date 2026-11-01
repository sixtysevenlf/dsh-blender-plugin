#!/usr/bin/env python
"""Emit function line counts for runtime/py and addon (JSON on stdout)."""
import ast
import json
import os
import sys


def main():
    root = sys.argv[1]
    out = []
    for base in (os.path.join(root, "runtime", "py"), os.path.join(root, "addon")):
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__",)]
            for name in sorted(filenames):
                if not name.endswith(".py"):
                    continue
                p = os.path.join(dirpath, name)
                rel = os.path.relpath(p, root).replace(os.sep, "/")
                try:
                    with open(p, encoding="utf-8") as fh:
                        tree = ast.parse(fh.read(), filename=p)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        end = getattr(node, "end_lineno", node.lineno)
                        out.append({
                            "file": rel,
                            "name": node.name,
                            "line": node.lineno,
                            "endLine": end,
                            "lines": end - node.lineno + 1,
                        })
    json.dump(out, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
