#!/usr/bin/env bash
# 独立盘后复盘（launchd/cron 拉起）——脱离 WorkBuddy automation 独立"活起来"。
#
# 设计要点（来自 docs/summary/ai-evolution.md §6 选型结论）：
# 1. launchd 环境变量很裸 → PATH 显式写全（claude 在 nvm 下，不在 /usr/bin）
# 2. --allowedTools 白名单收紧：无人值守跑挂住 = 等一个永远不会来的授权
# 3. --bare 跳过本机自动发现（hooks/插件），保证每次运行结果可复现
# 4. --output-format json → 拿 session_id 与 total_cost_usd 做审计与成本护栏
# 5. 幂等：产物按日期命名，重复触发覆盖而非追加（cron 迟早会重跑）
# 6. 交易日判定前置：非交易日直接退出，不浪费 token
#
# 用法：scripts/agent/run_review.sh            （手动跑一次，等价于定时任务）
#       launchd 见同目录 com.ashare.review.plist
set -euo pipefail

REPO="/Users/hezifeng/Desktop/project/ms/ashare-ai-trader"
LOG_DIR="$REPO/scripts/agent/logs"
mkdir -p "$LOG_DIR"

# launchd 的 PATH 极简，claude/python 都不在里面 —— 必须显式给全
export PATH="/Users/hezifeng/.nvm/versions/node/v24.14.0/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export NO_PROXY="*"          # 本机 3000/8000 与内网网关不走代理
export no_proxy="*"

cd "$REPO"

TODAY="$(date +%F)"
OUT="$LOG_DIR/review-$TODAY.json"

# ── 交易日判定（非交易日退出；日历来自后端哨兵数据，缺失时按周一~周五兜底）──
if ! "$REPO/backend/.venv/bin/python" - "$REPO/backend/data/trade_calendar.json" <<'PY'
import json, sys
from datetime import date
try:
    days = set(json.load(open(sys.argv[1])).get("days") or [])
except Exception:
    days = set()
today = date.today().isoformat()
if days:
    sys.exit(0 if today in days else 1)
sys.exit(0 if date.today().weekday() < 5 else 1)
PY
then
  echo "[$(date +%F\ %T)] 非交易日，跳过" >> "$LOG_DIR/run.log"
  exit 0
fi

# ── 复盘任务（prompt 自包含：未来运行看不到本会话）──
PROMPT="你是 A 股交易系统的盘后复盘助手。

严格遵循复盘框架 docs/kb/06-review-framework.md（七阶段：定界→事实链→周期定位→六维剖析→规律提炼→KB 对照→反例自检+自评）。

今日任务（交易日 ${TODAY}）：
1. 读 docs/kb/00-INDEX.md 与 docs/kb/01-stock-picking.md，列出与今日盘面相关的既有条目（按 KB-ID）；
2. 用 Bash 调本机后端取当日事实：curl --noproxy '*' http://127.0.0.1:8000/api/picks/today 、/api/market/sentiment 、/api/picks/morning-brief/today （拿不到就是拿不到，标「未判定」，禁止臆造）；
3. 按框架七阶段产出复盘，规律必须写成可证伪的 if-then（量化触发条件+适用边界+验证状态）；
4. 与既有 KB 条目冲突的地方显式写出冲突点（⚠️ 指向条目），不静默覆盖；
5. 输出 Markdown，落盘到 docs/daily-review/${TODAY}-agent.md，并在文末附六维自评表（每项 0-10，低分项写明下轮改进）。

红线：不提买卖建议、不改风控/资金/推送配置、不碰凭据、不删数据、不执行 git push。只用 Read/Grep/Bash(只读为主)，除写上述 md 文件外不写任何其他文件。

时间纪律：先跑 date 实测当前时间（会话注入时间不可信）。若早于 15:00（盘中数据未定稿），
必须在文档开头标注「盘中快照，非收盘定稿」，结论按未定稿处理——不得把盘中数据当终稿下结论。"

echo "[$(date +%F\ %T)] 开始盘后复盘" >> "$LOG_DIR/run.log"

# --allowedTools 白名单：给最小必要集（写文件靠 Write，取数靠 Bash(curl)）
claude -p "$PROMPT" \
  --allowedTools "Read,Grep,Glob,Write,Bash(curl:*)" \
  --permission-mode acceptEdits \
  --bare \
  --output-format json \
  > "$OUT" 2>> "$LOG_DIR/run.log" || {
    echo "[$(date +%F\ %T)] 复盘执行失败（见 $OUT）" >> "$LOG_DIR/run.log"
    exit 1
  }

# ── 成本与留痕（无人值守没有天然成本上限，每-run 必记）──
"$REPO/backend/.venv/bin/python" - "$OUT" "$LOG_DIR/run.log" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as exc:
    print(f"[成本读取失败] {exc}", file=sys.stderr); sys.exit(0)
cost = d.get("total_cost_usd")
turns = d.get("num_turns")
sid = d.get("session_id")
print(f"cost={cost} turns={turns} session={sid}")
PY

echo "[$(date +%F\ %T)] 复盘完成 → $OUT" >> "$LOG_DIR/run.log"
