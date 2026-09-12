"""因子库（docs/summary/factor-system.md）。

P0 范围 = 评估准入闭环：
- library.py：因子注册表（唯一口径锚，SQL 定义 + 类别 + 最小样本）；
- evaluate.py：评估引擎（RankIC/ICIR/五分层/分年/覆盖率/相关矩阵 + 三层判定）；
- scripts/run_factor_eval.py：跑批 CLI → data/factors/eval_report.json。

纪律：缺失=NULL 三态（次新/停牌/除权污染不凑数）；前瞻口径 T+1 收盘进场
（保守，剔除隔夜跳空虚增）；一字板样本剔除（买不进）；评估是离线批任务，
不在请求路径上；纯 DuckDB SQL（backend venv 无 pandas）。
"""
