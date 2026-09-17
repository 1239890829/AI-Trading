#!/usr/bin/env python3
"""Move reviewed project assets to artifacts/trash; never overwrite on restore."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid


def scoped(root: Path, path: Path) -> Path:
    # Resolve parents, not the leaf: a symlink itself can be safely moved.
    absolute = Path(os.path.abspath(path))
    if absolute.parent.resolve() != absolute.parent:
        raise ValueError("Symlinked parent is outside the supported scope")
    relative = absolute.relative_to(root)
    if not relative.parts or relative.parts[0] == ".git":
        raise ValueError("Project root and Git metadata are protected")
    if relative.parts[0] == "artifacts" and (len(relative.parts) == 1 or relative.parts[1] in {"backups", "trash"}):
        raise ValueError("Recovery copies and the trash are protected")
    return relative


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()

    def visit(item: Path, relative: str) -> None:
        digest.update(relative.encode() + b"\0")
        if item.is_symlink():
            digest.update(b"link\0" + os.readlink(item).encode())
        elif item.is_dir():
            digest.update(b"directory\0")
            for child in sorted(item.iterdir()):
                visit(child, relative + "/" + child.name)
        elif item.is_file():
            digest.update(b"file\0")
            with item.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            raise ValueError("Missing or unsupported entry")
        digest.update(b"\0end\0")

    visit(path, "")
    return digest.hexdigest()


def trash_root(root: Path) -> Path:
    target = root / "artifacts/trash"
    if target.resolve() != target:
        raise ValueError("Recovery destination must not traverse symlinks")
    return target


def write_record(journal: Path, record: dict) -> None:
    # A failed update after moving the payload must leave the prepared record
    # recoverable, not truncate the only copy of its original path and checksum.
    temporary = journal.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(journal)


def move(root: Path, path: Path, reason: str) -> str:
    relative = scoped(root, path)
    source = root / relative
    checksum = fingerprint(source)
    batch = uuid.uuid4().hex
    directory = trash_root(root) / batch
    directory.mkdir(parents=True, mode=0o700)
    journal = directory / "record.json"
    record = dict(original=relative.as_posix(), reason=reason, sha256=checksum,
                  created_at=datetime.now(timezone.utc).isoformat(), state="prepared")
    write_record(journal, record)
    # Neutral payload name avoids rebuilding platform-specific directory names.
    source.rename(directory / "payload")
    record["state"] = "stored"
    write_record(journal, record)
    return batch


def restore(root: Path, batch: str) -> Path:
    if len(batch) != 32 or any(c not in "0123456789abcdef" for c in batch):
        raise ValueError("Expected an exact entry ID from --list")
    directory = trash_root(root) / batch
    if directory.is_symlink():
        raise ValueError("Recovery entry must not be a symlink")
    journal = directory / "record.json"
    if journal.is_symlink():
        raise ValueError("Recovery record must not be a symlink")
    record = json.loads(journal.read_text())
    dest = root / scoped(root, root / record["original"])
    if os.path.lexists(dest):
        raise FileExistsError("Restore target exists; preserve both copies")
    payload = directory / "payload"
    if fingerprint(payload) != record["sha256"]:
        raise ValueError("Recovery checksum differs; retain for inspection")
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload.rename(dest)
    record["state"] = "restored"
    write_record(journal, record)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", type=Path, nargs="*")
    parser.add_argument("--reason")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--restore")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if (args.list or args.restore) and args.paths:
        parser.error("Do not combine paths with --list or --restore")
    if args.list:
        for journal in sorted(trash_root(root).glob("*/record.json")):
            if journal.parent.is_symlink() or journal.is_symlink():
                raise ValueError("Recovery listing must not traverse symlinks")
            print(journal.parent.name, journal.read_text())
    elif args.restore:
        print(restore(root, args.restore))
    else:
        if not args.paths or not args.reason:
            parser.error("Reviewed paths and --reason are required")
        for path in args.paths:
            scoped(root, path)
        for path in args.paths:
            print(move(root, path, args.reason))


if __name__ == "__main__":
    main()
