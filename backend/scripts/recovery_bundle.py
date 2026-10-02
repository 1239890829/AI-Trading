"""Explicit offline backup/verify/restore. No settings/.env or application startup."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from app.core.recovery import backup, restore, verify


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    b = commands.add_parser('backup')
    b.add_argument('--source-root', type=Path, required=True)
    b.add_argument('--destination', type=Path, required=True)
    b.add_argument('--quiescent', action='store_true', help='Operator confirms all declared data writers are stopped')
    b.add_argument('--code-head', required=True)
    v = commands.add_parser('verify')
    v.add_argument('--bundle', type=Path, required=True)
    r = commands.add_parser('restore')
    r.add_argument('--bundle', type=Path, required=True)
    r.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'backup':
        result = backup(args.source_root, args.destination, quiescent=args.quiescent, code_head=args.code_head)
        summary = {'state': 'backed_up', 'files': len(result['files']), 'seconds': result['backup_seconds']}
    elif args.command == 'verify':
        result = verify(args.bundle)
        summary = {'state': 'verified', 'files': len(result['files'])}
    else:
        summary = restore(args.bundle, args.destination)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'state': 'failed', 'error_type': type(exc).__name__, 'reason': str(exc)}), file=sys.stderr)
        raise SystemExit(1) from None
