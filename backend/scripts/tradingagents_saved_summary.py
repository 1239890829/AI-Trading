"""Explicitly derive small summaries in this installation's saved-research root."""
from __future__ import annotations

import argparse
import json
import re


def main(argv=None) -> int:
    from app.services import tradingagents_research as research
    from app.services._tradingagents_summary import materialize

    parser = argparse.ArgumentParser(description="核验已存完整研究并派生小摘要；默认dry-run，不调用模型或修改原件")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--symbol", action="append", help="仅处理该六位代码，可重复")
    scope.add_argument("--all-saved", action="store_true", help="仅扫描本安装research ROOT下的六位证券目录")
    parser.add_argument("--write", action="store_true", help="核验成功后写derived/latest-summary.json；原件和DB不变")
    args = parser.parse_args(argv)
    if research.ROOT.is_symlink():
        parser.error("research ROOT不可为符号链接")
    symbols = sorted(set(args.symbol or [p.name for p in (research.ROOT.iterdir() if research.ROOT.exists() else ())
                                        if p.is_dir() and re.fullmatch(r"[0-9]{6}", p.name)]))
    if any(not re.fullmatch(r"[0-9]{6}", symbol) for symbol in symbols):
        parser.error("symbol必须是六位代码")
    failed = 0
    for symbol in symbols:
        try:
            result = materialize(symbol, write=args.write)
            # Compact identities only; never print full reports, model answers or paths.
            print(json.dumps({key: result[key] for key in ("symbol", "run_id", "summary_sha256", "state")}, ensure_ascii=False))
        except (OSError, ValueError, TypeError, KeyError):
            failed += 1
            print(json.dumps({"symbol": symbol, "state": "unavailable", "reason": "原件核验未通过；未写摘要"}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
