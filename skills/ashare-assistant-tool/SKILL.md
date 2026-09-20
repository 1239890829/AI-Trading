---
name: ashare-assistant-tool
description: 给 ashare-ai-trader 的全局助手新增/修改一个受限工具（只读或「现算不留存」的执行型）的完整落地契约——handler、ToolSpec、中文标签、提示词能力清单、不触网测试、口径断言、注入验证、真实数据冒烟、门禁与提交。当用户说「助手查不到 X」「把 X 接给助手」「助手答没有这项数据」，或台账 P2-28 那类「系统有、助手取不到」的能力空窗时使用。
read_when:
  - 要给 /assistant 增删工具，或某个数据面「系统明明有、助手答没有」
  - 改动 app/assistant/tools.py 的 TOOL_SPECS / TOOL_LABELS / _t_* handler
  - 改 app/assistant/prompt.py 的能力清单（CAPABILITY_TOOLS）
agent_created: true
---

# 给助手加一个受限工具（ashare-ai-trader）

**一句话**：助手能力的完成度 = **提示词认识它 + 工具能取到 + 输出带口径**，三者缺一个都等于没接。

## 0. 先做两查（省掉一半返工）

1. **查是否已有等价工具**：`grep "^    \"" backend/app/assistant/tools.py` 看 TOOL_SPECS 键集（现有 30+ 个）。
   「新建 X」常常其实是「复用既有挂载点」。
2. **查底层是否真有可读数据**：
   ```bash
   # 有持久化产物 → 直接读（零成本）
   # 只有现算端点 → 走「现算不留存」，但必须先实测耗时
   ```
   ⚠️ **别默认有结果表**。2026-09-12 实测：回测在生产侧**根本没有结果表**
   （`POST /api/backtest/run` 同步计算、不持久化）⇒「读现成结果」这条路不存在。
   **新建结果表属「写库 + schema 变更」= 需用户确认的红线**，默认不走。

## 1. 六步落地（顺序不能跳）

| # | 位置 | 要点 |
|---|---|---|
| 1 | `tools.py` → `async def _t_xxx(ctx, **kw)` | 重依赖**函数内 lazy import**；阻塞调用（网络/Parquet/回测）用 `await asyncio.to_thread(...)` 包一层 |
| 2 | `tools.py` → `TOOL_SPECS` | **名字不得撞既有键**（实测踩过：想叫 `themes` 但已被「题材梯队」占用，pyflakes 帮抓到）；`desc` 里就要写清口径限定 |
| 3 | `tools.py` → `TOOL_LABELS` | 漏加会**静默显示英文名**（进度提示与回执都用它）；守卫 `test_tool_labels_cover_every_spec` |
| 4 | `prompt.py` → `CAPABILITY_TOOLS` | **不加就等于没接**：能力清单里没点名，模型会照旧答「我没有这项数据」（KB-ENG-49 的根因就是提示词自我否认） |
| 5 | `tests/test_assistant.py` | 用 `_call_tool(_ctx(_P()), "name", ...)`，**不触网**（打桩 provider / `monkeypatch.setattr` 掉取数函数） |
| 6 | 门禁 + 真实数据冒烟 | 见 §4 |

## 2. 三条内容纪律（测试要钉死，注入验证要能打红）

1. **取不到就如实说，绝不编造**——服务不可用 / 目录为空 / 样本不足，都要回「取不到 / 不足」，
   **不许回一个看起来合理的数字**（用户会照着假数据做决策，比说不知道危险得多）。
2. **「能力缺口」不得说成「事实」**——典型：某取数只有单一源（`get_auction_snapshot` 仅 ths），
   取不到时必须说「该维度数据源覆盖不到」，不能只说「没有数据」，否则模型会告诉用户「这只票没有竞价」。
3. **口径必须随结论一起给**——回测类=「历史统计事实·不构成买卖建议·样本内·未做参数优化·不得外推」；
   索引类=「人工映射索引，非已验证规律」；评估类=「样本内，未做样本外验证」。
   **口径不是免责声明，是结论的一部分**，删掉它这条输出就是错的。

## 3. ⚠️ 数值呈现必须按「量的语义」分档（KB-ENG-64）

**真实数据一跑就暴露、桩数据与单测全看不见。** 2026-09-12 实测踩到两处：

```python
# ❌ 收益率与比率共用一个 _pct()，一律加 "+"
f"最大回撤 {_pct(report.max_drawdown)}"     # → "最大回撤 +16.90%"  读起来是「涨了 16.9%」，方向反了
f"胜率 {_pct(report.win_rate)}"             # → "胜率 +12.50%"      比率不该带符号
f"平均持有 {extra['avg_holding_bars']}"      # → "平均持有 8.461538" 浮点未截断

# ✅ 按量的语义分档 + 定小数位
def _pct(v, sign=True):
    n = float(v) * 100
    return f"{n:+.2f}%" if sign else f"{n:.2f}%"
# 有方向：区间收益 / 超额 / 年化 / 样本内外 → sign=True
# 无方向或天然负极：最大回撤 / 波动率 / 胜率 / 盈亏比 → sign=False
```

**判据**：写任何数值呈现前先问一句「**这个量有方向吗**」。
**断言也必须升级**：`assert "最大回撤" in out` **对方向不敏感**，要写
`assert "最大回撤 +" not in out`、`assert re.search(r"胜率 \+", out) is None`。

## 4. 验收：桩 → 注入 → 真实数据，三步都要

```bash
cd backend
# ① 桩数据单测（快、可重复）
.venv/bin/python -m pyflakes app/assistant/tools.py app/assistant/prompt.py tests/test_assistant.py
.venv/bin/pytest tests/test_assistant.py -q --basetemp=/tmp/pytest-basetemp -k "<新工具名>"

# ② 注入验证：把一条口径/一处分档改坏，确认它精确变红，然后还原
#    （写在一段 python 脚本里做「改 → 跑 → 还原」，比手工两步可靠）

# ③ ⚠️ 真实数据冒烟：桩数据只证明「跑得通」，不证明「读得对」
.venv/bin/python - <<'PY'
import asyncio, sys, time; sys.path.insert(0, ".")
from app.assistant.tools import ToolCall, ToolContext, run_tool
ctx = ToolContext(provider=None, known_symbols={"600519"})
t = time.time()
print(asyncio.run(run_tool(ToolCall(name="backtest",
      args={"symbol": "600519", "strategy": "ma_cross", "bars": "250"}), ctx, cache=None)))
print(f"{time.time()-t:.2f}s")
PY
```

**这一步是硬要求**，必须肉眼读一遍输出——符号、单位、量纲、小数位只有这一关能查出来。

## 5. 收尾

```bash
# 全量门禁（注意别叠 -q：addopts 已有 -q，叠了变 -qq 会吞掉汇总行）
cd backend && .venv/bin/pytest --basetemp=/tmp/pytest-basetemp --junitxml=/tmp/be-full.xml
cd apps/web && npx tsc --noEmit && npx eslint . && CODEBUDDY_SAFE_DELETE_ENABLED=0 npx vitest run
cd .. && python3 scripts/doc-health.py
```

回填 `AGENTS.md` §1 门禁行的测试数（**实测，不凭记忆**）。

## 反模式（都实测踩过）

- ❌ **同文件发多个 Edit 并行** → **必丢一条且报「成功」**。2026-09-12 对 `tools.py` 同时改
  `TOOL_SPECS` 与 `TOOL_LABELS`，后者生效前者静默丢失，靠测试报「未登记的工具名」才发现。**同文件一律串行。**
- ❌ 只加 handler 不加 `TOOL_LABELS` / 提示词能力清单 → 功能在、用户摸不到。
- ❌ 用桩数据下结论说「已验证」→ 方向反了都看不出来。
- ❌ 为了让「取不到」好看而回默认值 / 空数组 → 把能力缺口伪装成事实。
- ❌ 为了接线而新建结果表 → 写库 + schema 变更，属需确认项，先想「现算不留存」。
- ❌ 用 shell `grep` 做检索 → BSD 下静默返空（KB-ENG-04）。**一律用 Grep 工具。**
