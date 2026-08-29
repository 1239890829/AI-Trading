# AGENTS.md — AI 开发者交接入口（workbuddy 必读）

你接手的是 **AShare AI Trader**：A 股实时行情 + 量化投研 + 模拟交易工作台。
本文件是你的作业手册。**动手前先读完，然后读 `docs/PROJECT-MASTER.md`（全项目总整理，唯一总览）。**

---

## 0. 红线（违反即事故）

1. **禁止**连接真实券商 / 自动真实下单。系统只有模拟交易（`/api/paper/*`）。
2. **禁止**把 mock 数据、过期缓存冒充实盘。数据源失败 → 标 `stale` + health=degraded。
3. **禁止**输出确定性买卖结论（必涨/稳赚）。技术结论只给偏向 + 依据 + 失效条件。
4. **API Key 只存 `backend/.env`**（已 gitignored），绝不入库/入前端/入文档。
5. 撮合规则（T+1/涨跌停拒/整手/费用/停牌拒）是硬拦截，不可绕过。

## 1. 快速启动

```bash
# 后端（Python 3.11，venv 已建好）
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000        # 读 backend/.env（含 THS key）

# 前端（node_modules 已装）
cd apps/web && npm run dev                        # http://localhost:3000/workbench

# 测试与门禁（每次改动全部跑，全绿才算完）
cd backend && .venv/bin/pytest                    # 70 用例
cd apps/web && npx tsc --noEmit && npx next lint  # 双零
cd backend && .venv/bin/python -m pyflakes app tests
```

## 2. 工作方式（前任验证过的教训，勿重蹈覆辙）

- **第一原则：验收以实际看到的为准，不靠推理。** 涉及 UI / 布局，不清楚就截图看。
  禁止用"读代码 + 算宽度 + 想当然"代替观察——同一天在这上面栽过两次（后端没重启以为没生效、
  tab 行被挤爆没看出来）。详见 §2.2 截图验收。
- **每阶段流程**：实测数据源（curl 先行）→ 小切片实现 → pytest 全绿 → 浏览器截图验收（Next 徽章必须 0 issues）→ git checkpoint（可回退）。
- **禁止在 dev server 运行时执行 `next build`**（.next 冲突已踩两次）。类型检查用 `npx tsc --noEmit`。
- **复杂 JSX 改动整文件重写**，不要字符串补丁（已三次把结构改坏，靠 git checkout 止损）。
- **长内容写脚本文件执行**，不要超长 heredoc（终止符/引号嵌套踩过多次）。
- pip 装包走清华镜像 `-i https://pypi.tuna.tsinghua.edu.cn/simple`；行情 httpx 客户端保持 `trust_env=False`。
- 用户系统代理在 127.0.0.1:7897（SOCKS）：只用于 GitHub 等外网；curl 本地 API 记得 `--noproxy '*'` 或 `env -u http_proxy`。
- **改完后端必须验证「用户正在跑的那个实例」**，不能只在临时端口起新实例验完就交付。8000 上若不是 `--reload` 启动，改完不重启就仍是旧代码；而前端一旦有兜底分支（如缺 `board_groups` 回退扁平列表），页面会与改动前**一模一样**，看起来像"功能没生效"而非"后端没重启"。验收要先打 8000：`curl -s --noproxy '*' http://127.0.0.1:8000/api/xxx` 看新字段在不在。
- 每阶段收尾：更新 `docs/PROJECT-MASTER.md` §十二阶段表 + README 路线图 + 清扫页面过时提示。

### 2.1 接新数据源五步法（改 Provider / 加字段前必走）

1. **curl 先行**：带齐 header（`Referer`/`User-Agent`）直打，把响应存文件再分析，别凭印象写解析。
2. **记录字段口径，尤其是类型**：同一响应里类型可能不一致。实测教训——东财 `ssbk` 的
   `IS_PRECISE` 是**字符串** `'0'/'1'`（还可能 `null`），而同级的 `BOARD_RANK` 是整数。
   写 `== 1` 会静默全部失配、不抛错，症状是分类结果全落进兜底组。比较前一律 `str(x) == "1"`。
3. **找规律要多采样**：至少拉 5–6 只不同行业/市场的股票交叉验证，看排序与分段是否稳定。
   数据源常不给类别字段，但可能**按序号天然分段**（详见 docs/data-sources.md §3.1）。
4. **fixture 从实抓数据生成，不要手写**：用 `json.load` 后裁掉无关字段再落盘。
   手写 fixture 极易编出不存在的形状——已踩过：把地域放在 rank 2、风格放在 rank 3，
   结果 2 个用例失败，而失败的是 fixture 不是代码。
5. **写进 docs/data-sources.md**：字段口径、类型陷阱、分段规律，下一个人别再踩一遍。

### 2.2 截图验收（UI 改动的唯一验收标准）

**只要动了前端，交付前必须截图看一眼。** 用户 2026-08-29 明确要求："不清楚布局就截图看，
以后都要这样，以实际看到的为准。"

```bash
agent-browser open "http://127.0.0.1:3000/workbench?symbol=600519"
agent-browser wait --load load          # networkidle 在 SPA 上会挂，用 load
agent-browser screenshot /tmp/xxx.png   # 位置参数，不是 --path
agent-browser close                     # 收尾必须关，否则留僵尸 Chromium
```

要点：
- **看内容，别看代码**。本项目有过 markers 数组构建完却从未调 `setMarkers()` 的情况——
  代码看着齐全，界面上一个点都没有。
- **布局问题必须截图**。右列固定 300px，往里塞东西前先截图确认放不放得下，
  不要靠心算宽度。曾因在 5 个 tab 后 `ml-auto` 塞来源时间把整行挤变形。
- 交互态（tab 切换、展开收起、弹窗）要切过去截，初始页面看不到。
- `agent-browser click "text=资料"` 这类文本选择器可能匹配不到或匹配多个，
  先用 `agent-browser snapshot -i` 拿 ref 再点。

## 3. 文档地图（按需读）

| 文档 | 内容 |
|---|---|
| **docs/PROJECT-MASTER.md** | 总览：技术栈/目录逐文件/数据源口径/32 API/前端/交易系统/测试/配置/坑/阶段状态 |
| docs/architecture.md | 分层架构与数据管线 |
| docs/data-sources.md | 四源字段口径实测记录（改 Provider 前必读） |
| docs/api.md / websocket.md | API 与 WS 契约 |
| docs/backtest-rules.md | 回测强制禁令（做回测前必读，代码级禁令） |
| docs/sentiment.md / longhu.md | 情绪与龙虎榜口径（**改情绪模块前先读下面的复盘**） |
| **docs/sentiment-phase-review.md** | **情绪周期：业界判据调研 + 2026-08-29「高潮」误判复盘 + P0/P1/P2 优化清单** |
| docs/risk-management.md / mcp.md | 风控红线 / MCP 规划 |
| docs/ui-redesign-plan.md | 布局 v3 规划与 L2 边界结论 |
| docs/retro-and-gaps.md | **欠缺清单（你的待办池）** |
| docs/deployment.md | 部署 + 已踩坑清单 |

## 4. 技能库（skills/，随仓库走）

| 技能 | 用途 | 触发时机 |
|---|---|---|
| `skills/impeccable/` | UI 设计语言（v4.1，23 命令）。本项目定位 **Operate 模式**：可扫读性>表达，品牌在细节 | 任何 UI 改动前读 craft-floor；动效读 animate.md（"一个署名动效"原则已用于价格 tick 闪烁） |
| `skills/design-taste/` + `skills/taste-skill/` | Anti-Slop 设计审计、极简协议 | UI 改动后对照禁令清单（禁 emoji 图标/渐变/玻璃拟态/大阴影） |
| `skills/gsap-skills/` | GSAP 动画（8 子技能） | Phase 6 历史回放的时间线控制时启用；现在不要引入 gsap 依赖 |
| `skills/hithink-finance/` | 同花顺官方数据服务（59 端点，REST/MCP/CLI/SDK） | 扩展数据能力时；**不含 L2/tick/分钟K**（官方声明） |

## 5. 当前状态与你的待办（按优先级）

**已完成**：Phase 1-4 全部；Phase 5 部分（多因子技术评估）；Phase 6 核心（撮合引擎 + 交易页签 + 真实 B/S 点）。
快照：70 测试全绿 · 32 REST + 1 WS · 四源链 `ths→tencent→eastmoney→sina` · 33+ commits。

**你的待办（按序，做完一项在 docs/retro-and-gaps.md 划一项并 git checkpoint）**：

1. **Phase 6 收尾**：持仓成本线画上 K 线 / 成交记录列表 / 重置账户按钮（engine/positions API 已就绪，`app/paper/engine.py`）
2. **Phase 4 补漏**：概念题材 chips 过滤风格标签；新闻/公告已接（东财），缺 AI 摘要（Phase 7）
3. **Phase 5**：全市场选股器（快照已有，5550 只）→ 评分系统（复用 `lib/technical-analysis.ts` 与后端 sentiment 模式：可解释+依据+置信度）
4. **Phase 6 后半**：回测引擎——**先读 docs/backtest-rules.md 强制禁令，防泄露测试先行**
5. **Phase 8**：预警通知、Next 升级、error.tsx 错误边界
6. 技术债清 单见 docs/retro-and-gaps.md §三（StockDetailPanel 拆分优先）

## 6. 关键常识

- Provider 链 `ths→tencent→eastmoney→sina` 逐方法 failover；加新数据源 = 实现协议 + 注册 factory + 加链
- 质量五级：high/medium/low/stale/invalid；low 及以下 AI 禁用、回测禁用、前端强制标识
- 交易撮合在 `app/paper/engine.py`（费用/T+1/涨跌停全配置化）；涨跌停价 ths 缺失时由 main.py live_quote 从腾讯补
- Parquet 快照每 5 分钟落 `data/parquet/snapshots/`（回测地基）
- 东财 push2 本机被 WAF 限流：行情走腾讯，特殊数据走 datacenter/push2ex（稳定）
