"""因子评估跑批 CLI（docs/factor-library-design.md §6 P0）。

用法（cwd 任意）：
    backend/.venv/bin/python backend/scripts/run_factor_eval.py
    # 可选 --db / --out 覆盖默认路径
输出：backend/data/factors/eval_report.json + 控制台摘要表。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.factors.evaluate import run_full_eval  # noqa: E402

DEFAULT_DB = BACKEND_ROOT / "data" / "marketdb" / "market.duckdb"
DEFAULT_OUT = BACKEND_ROOT / "data" / "factors" / "eval_report.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="因子库评估跑批")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    report = run_full_eval(args.db, out_path=args.out)

    s = report["summary"]
    print(f"\n=== 因子评估摘要（{report['universe']['stocks']} 只 / "
          f"{report['universe']['range'][0]} ~ {report['universe']['range'][1]}）===")
    for r in report["factors"]:
        w = r["windows"].get(str(r.get("best_horizon")), {})
        cov = f"{r['coverage']:.1%}" if r.get("coverage") is not None else "--"
        extra = f" ← 与 {r['redundant_with']} 高度相关" if r.get("redundant_with") else ""
        print(
            f"[{r['verdict']:11}] {r['name']:10} 主窗 {r.get('best_horizon', '--'):>2}日"
            f"  IC={w.get('ic_mean', '--'):+.4f}  ICIR={w.get('icir', '--'):+.2f}"
            f"  覆盖={cov}{extra}"
        )
        if r["verdict"] == "FAIL":
            for reason in r["reasons"][:2]:
                print(f"              · {reason}")
    print(f"\nPASS={s['pass']} CONDITIONAL={s['conditional']} FAIL={s['fail']}")
    print(f"报告：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
