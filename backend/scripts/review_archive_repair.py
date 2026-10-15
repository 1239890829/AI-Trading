"""Narrow, recoverable repair of archive-only review statuses; dry-run by default."""
import argparse
import json
from pathlib import Path
import sqlite3

from app.core.db import get_session_factory
from app.review.archive_repair import repair_archive_dispositions, restore_archive_dispositions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply-digest')
    mode.add_argument('--restore-digest')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    sf = get_session_factory()
    if args.apply_digest or args.restore_digest:
        if not args.backup or args.backup.exists():
            parser.error('写入前必须指定不存在的 --backup 文件')
        with sf() as db:
            engine = db.get_bind()
            if engine.dialect.name != 'sqlite' or not engine.url.database:
                parser.error('此操作工具仅支持本地SQLite')
            source_path = Path(engine.url.database).resolve()
        if source_path == args.backup.resolve():
            parser.error('备份不能覆盖原库')
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        # Explicit read-only URI. Backup is consistent even when other app readers run.
        with sqlite3.connect(source_path.as_uri()+'?mode=ro', uri=True) as source:
            with sqlite3.connect(args.backup) as backup:
                source.backup(backup)
    result = restore_archive_dispositions(sf, args.restore_digest) if args.restore_digest else repair_archive_dispositions(sf, expected_digest=args.apply_digest)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
