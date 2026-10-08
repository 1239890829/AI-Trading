"""免费TDX历史日线归档/完整性核验；不导入marketdb，不启动后台任务。

backend/.venv/bin/python backend/scripts/archive_tdx_daily.py --date 2026-09-30
加 --package 本地.zip 可离线归档；--verify 仅核已有归档。
"""
import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.market.tdx_daily_package import archive_package, download_package, verify_archive  # noqa: E402


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',type=date.fromisoformat,required=True)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2]/'data/parquet/archives/tdx_daily')
    parser.add_argument('--package',type=Path)
    parser.add_argument('--verify',action='store_true')
    args=parser.parse_args()
    if args.verify:
        result=verify_archive(args.root/args.date.strftime('%Y%m%d'))
    else:
        raw = args.package.read_bytes() if args.package else download_package(args.date)
        result=archive_package(raw,args.date,args.root,received_at=None if args.package else datetime.now(timezone.utc))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
