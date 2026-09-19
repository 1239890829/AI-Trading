"""Print historical TypeSafe/Jev metadata-only usage summary (zero network calls)."""
from __future__ import annotations

import argparse
import json

from app.core.jev_client import historical_usage_summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=None, help="Optional usage.jsonl path override")
    args = ap.parse_args(argv)
    print(json.dumps(historical_usage_summary(args.path), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
