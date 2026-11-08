"""Package checks for the add-on zip (T10, release-machine only).

Reads the committed add-on zip manifest and runs Blender's extension validator.
The zip now carries blender_manifest.toml in its single package root, so the
validator must pass and bl_info must be absent. The real install/enable check
stays in tests/e2e/blender/test_addon_e2e.py.

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
ZIP_PATH = REPO_ROOT / "extension" / "dsh_blender_bridge.zip"
PACKAGE_DIR = REPO_ROOT / "extension" / "dsh_blender_bridge"
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
        schema_source = archive.read(PACKAGE_ROOT + "/gateway_schema.py").decode("utf-8")
        manifest_source = archive.read(
            PACKAGE_ROOT + "/blender_manifest.toml"
        ).decode("utf-8")
    package_files = sorted(
        path.relative_to(PACKAGE_DIR).as_posix()
        for path in PACKAGE_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    expected = sorted(
        [PACKAGE_ROOT + "/" + relative for relative in package_files]
        + [PACKAGE_ROOT + "/blender_manifest.toml"]
    )
    return {
        "zip": str(ZIP_PATH),
        "entries": len(names),
        "manifest_matches_package": names == expected,
        "package_root_init": PACKAGE_ROOT + "/__init__.py" in names,
        "manifest_present": PACKAGE_ROOT + "/blender_manifest.toml" in names,
        "manifest_version": 'version = "2.0.0"' in manifest_source,
        "manifest_id": 'id = "dsh_blender_bridge"' in manifest_source,
        "old_single_file_absent": PACKAGE_ROOT + ".py" not in names,
        "no_cache_files": not any(
            "__pycache__" in name or name.endswith(".pyc") for name in names
        ),
        "bl_info_absent": "bl_info" not in init_source,
        "protocol_version": "ADDON_PROTOCOL_VERSION = 12" in schema_source,
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
        "ok": completed.returncode == 0,
        "output_tail": output.strip().splitlines()[-4:],
        "reason": (
            "validator accepted the extension manifest"
            if completed.returncode == 0
            else "validator rejected the extension manifest; inspect output_tail"
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
        and checks["manifest_present"]
        and checks["manifest_version"]
        and checks["manifest_id"]
        and checks["old_single_file_absent"]
        and checks["no_cache_files"]
        and checks["bl_info_absent"]
        and checks["protocol_version"]
        and report.get("extension_validate", {}).get("ok") is True
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
