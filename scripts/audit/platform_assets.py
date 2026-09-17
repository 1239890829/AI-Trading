#!/usr/bin/env python3
"""Inventory and verify a recovery copy of project-local legacy assets (MIG-0).

Never follows symlinks or removes sources. The output contains private local
assets and belongs in ignored artifacts/backups, never in Git.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile

SOURCES = {".workbuddy": "primary", ".workbuddy-ai": "adapter"}


def inventory(root: Path) -> list[dict]:
    tracked = set(subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "-z"], text=True,
    ).split("\0"))
    records = []
    for source, archive_root in SOURCES.items():
        base = root / source
        if base.is_symlink():
            raise ValueError(f"Source root must not be a symlink: {source}")
        if not base.is_dir():
            continue
        for parent, dirs, files in os.walk(base, followlinks=False):
            for name in sorted(dirs + files):
                path = Path(parent) / name
                info = path.lstat()
                relative = path.relative_to(root).as_posix()
                if stat.S_ISLNK(info.st_mode):
                    kind, content = "symlink", os.readlink(path).encode()
                elif stat.S_ISREG(info.st_mode):
                    kind, content = "file", path.read_bytes()
                elif stat.S_ISDIR(info.st_mode):
                    kind, content = "directory", b""
                else:
                    raise ValueError(f"Unsupported filesystem entry: {relative}")
                ignored = subprocess.run(
                    ["git", "-C", str(root), "check-ignore", "-q", "--", relative],
                    check=False,
                ).returncode == 0
                records.append({
                    "source": relative, "kind": kind, "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "mode": stat.S_IMODE(info.st_mode),
                    "link_target": content.decode() if kind == "symlink" else None,
                    "git": "tracked" if relative in tracked else "ignored" if ignored else "untracked",
                    "recovery_member": archive_root + "/" + path.relative_to(base).as_posix(),
                    "target": None, "review": "unreviewed", "consumer": "pending review",
                    "purpose": "pending review", "license": "pending review",
                    "disposition": "retain source until content and consumers are reviewed",
                })
    return sorted(records, key=lambda item: item["source"])


def backup(root: Path, output: Path) -> dict:
    root, output = root.resolve(), output.resolve()
    if any(output.is_relative_to(root / source) for source in SOURCES):
        raise ValueError("Recovery output must be outside every source tree")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    records = inventory(root)
    if not records:
        raise ValueError("No assets found; empty inventory is not migration evidence")
    archive = output / "recovery.tar.gz"
    with tarfile.open(archive, "w:gz", dereference=False) as bundle:
        for record in records:
            bundle.add(root / record["source"], arcname=record["recovery_member"], recursive=False)
    archive.chmod(0o600)
    # Read every saved payload back; never extract untrusted links into the host.
    with tarfile.open(archive) as bundle:
        for record in records:
            member = bundle.getmember(record["recovery_member"])
            if record["kind"] == "symlink":
                content = member.linkname.encode()
            elif record["kind"] == "file":
                with bundle.extractfile(member) as stream:
                    content = stream.read()
            else:
                content = b""
            if hashlib.sha256(content).hexdigest() != record["sha256"]:
                raise ValueError(f"Recovery mismatch: {record['source']}")
    if inventory(root) != records:
        raise ValueError("Source changed during backup; preserve this copy and retry in a new directory")
    manifest = {
        "source_root": str(root),
        "base_sha": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "recovery_payloads_verified": len(records), "records": records,
    }
    target = output / "manifest.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    target.chmod(0o600)
    return {key: value for key, value in manifest.items() if key != "records"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(backup(args.source.resolve(), args.output.resolve()), indent=2))
