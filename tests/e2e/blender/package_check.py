"""Package checks for the add-on zip (T10, release-machine only).

Reads the committed add-on zip manifest and runs Blender's extension validator
for the record. The add-on is a legacy bl_info package, not a Blender extension,
so extension validate is expected to report a missing blender_manifest.toml;
that is recorded as "inapplicable", never as a pass. The real validation is the
install test in tests/e2e/blender/test_addon_e2e.py.

Run: python tests/e2e/blender/package_check.py [--blender EXE]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ZIP_PATH = REPO_ROOT / "addon" / "dsh_blender_bridge.zip"
PACKAGE_DIR = REPO_ROOT / "addon" / "dsh_blender_bridge"
PACKAGE_ROOT = "dsh_blender_bridge"


def zip_manifest():
    with zipfile.ZipFile(ZIP_PATH) as archive:
        entries = [
            {"name": info.filename, "size": info.file_size}
            for info in archive.infolist()
        ]
    return entries


def inspect_zip():
    with zipfile.ZipFile(ZIP_PATH) as archive:
        names = archive.namelist()
        init_source = archive.read(PACKAGE_ROOT + "/__init__.py").decode("utf-8")
        protocol_source = archive.read(PACKAGE_ROOT + "/protocol.py").decode("utf-8")
    package_files = sorted(
        path.relative_to(PACKAGE_DIR).as_posix()
        for path in PACKAGE_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    expected = [PACKAGE_ROOT + "/" + relative for relative in package_files]
    return {
        "zip": str(ZIP_PATH),
        "entries": len(names),
        "manifest_matches_package": names == expected,
        "package_root_init": PACKAGE_ROOT + "/__init__.py" in names,
        "old_single_file_absent": PACKAGE_ROOT + ".py" not in names,
        "no_cache_files": not any(
            "__pycache__" in name or name.endswith(".pyc") for name in names
        ),
        "bl_info_version": "(1, 11)" in init_source,
        "protocol_version": "ADDON_PROTOCOL_VERSION = 11" in protocol_source,
    }


def run_extension_validate(blender):
    command = [str(blender), "--command", "extension", "validate", str(ZIP_PATH)]
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    return {
        "command": command,
        "exit_code": completed.returncode,
        "output_tail": output.strip().splitlines()[-4:],
        "applicable": "blender_manifest.toml" not in output,
        "reason": (
            "legacy bl_info add-on zip: extension validate requires blender_manifest.toml; "
            "the real validation is the install/enable E2E"
            if "blender_manifest.toml" in output
            else "validator accepted or produced another message; inspect output_tail"
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", default="")
    args = parser.parse_args(argv)
    report = {"zip": inspect_zip(), "manifest": zip_manifest()}
    blender = args.blender or "blender"
    try:
        report["extension_validate"] = run_extension_validate(blender)
    except Exception as exc:
        report["extension_validate"] = {"error": str(exc)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    checks = report["zip"]
    ok = (
        checks["manifest_matches_package"]
        and checks["package_root_init"]
        and checks["old_single_file_absent"]
        and checks["no_cache_files"]
        and checks["bl_info_version"]
        and checks["protocol_version"]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
