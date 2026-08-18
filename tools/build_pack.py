#!/usr/bin/env python3
"""
Build the distributable pack zip and refresh versions.json for auto-update.

    python3 tools/build_pack.py                     # build dist/<uid>_<version>.zip
    python3 tools/build_pack.py --changelog "..."   # ...and add a versions.json entry

The version comes from manifest.json, so bump it there first. PopTracker fetches
versions.json from the manifest's versions_url, compares package_version against
the installed pack, and downloads download_url when it is newer -- so the entry
must carry the sha256 of the exact zip attached to the GitHub release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.abspath(os.path.join(HERE, ".."))

# Everything the tracker needs at runtime; nothing else ships to players.
INCLUDE_DIRS = ["images", "items", "layouts", "locations", "maps", "scripts"]
INCLUDE_FILES = ["manifest.json", "settings.json", "README.md", "LICENSE"]

EXCLUDE_SUFFIXES = (".pdn", ".DS_Store", "Thumbs.db", ".pyc")
EXCLUDE_DIR_NAMES = {".git", ".github", "__pycache__", "tools", "dist", "tradebak"}

DEFAULT_REPO = "imklubb/no100f-poptracker"


def pack_files():
    for name in INCLUDE_FILES:
        path = os.path.join(PACK, name)
        if os.path.exists(path):
            yield path, name
    for top in INCLUDE_DIRS:
        root_dir = os.path.join(PACK, top)
        if not os.path.isdir(root_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(root_dir):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIR_NAMES)
            for fn in sorted(filenames):
                if fn.endswith(EXCLUDE_SUFFIXES) or fn in EXCLUDE_SUFFIXES:
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, PACK).replace(os.sep, "/")
                yield full, rel


def build_zip(out_path: str) -> str:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    count = 0
    # Deterministic: sorted entries and a fixed timestamp, so rebuilding an
    # unchanged pack produces an identical sha256.
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for full, rel in sorted(pack_files(), key=lambda p: p[1]):
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(full, "rb") as f:
                zf.writestr(info, f.read())
            count += 1
    print(f"packed {count} files -> {out_path}")
    return out_path


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO))
    ap.add_argument("--changelog", action="append", default=None,
                    help="changelog line for versions.json (repeatable)")
    ap.add_argument("--no-versions", action="store_true", help="build the zip only")
    args = ap.parse_args()

    manifest = json.load(open(os.path.join(PACK, "manifest.json"), encoding="utf-8"))
    version = manifest["package_version"]
    uid = manifest.get("package_uid", "pack")

    zip_name = f"{uid}_{version}.zip"
    out_path = os.path.join(PACK, "dist", zip_name)
    build_zip(out_path)
    digest = sha256(out_path)
    print(f"sha256 {digest}")

    if args.no_versions:
        return 0

    versions_path = os.path.join(PACK, "versions.json")
    if os.path.exists(versions_path):
        data = json.load(open(versions_path, encoding="utf-8"))
    else:
        data = {"versions": []}
    entries = [v for v in data.get("versions", []) if v.get("package_version") != version]

    entry = {
        "package_version": version,
        "download_url": f"https://github.com/{args.repo}/releases/download/v{version}/{zip_name}",
        "sha256": digest,
        "changelog": args.changelog or [f"Version {version}"],
    }

    # PopTracker treats the first entry as the newest.
    data["versions"] = [entry] + entries
    with open(versions_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
        f.write("\n")
    print(f"updated {versions_path} ({len(data['versions'])} versions)")
    print(f"  download_url {entry['download_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
