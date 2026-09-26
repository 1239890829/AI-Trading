---
name: ui-state-verify
description: 验收前端条件状态、对象上下文和跨页恢复；用可控响应复现空错陈旧、乱序与罕见分支，并区分 DOM、截图、Canvas、数值及源码证据。适用于 SPA 与 REST 界面。
---

# UI 状态与连续体验验收

适用于新改空态、错误/降级态、分档变体或跨页上下文；难以自然出现的状态用受控响应复现，最终仍核真实渲染。

**问题**：条件渲染分支（`items.length === 0` / `data.date == null` / `strip_buy_range === false`）
只有在特定数据状态下才出现。等它在真实环境自然出现 = 无限期不验收；只读代码 = 用推理代替观察。

**解法**：把接口响应钉住（stub），把罕见的**数据状态**变成可控、可重复的**渲染场景**。
优先使用当前可用的浏览器控制工具；下方 agent-browser 命令是既有可复现实例，不要求所有宿主都安装它。真实数据与桩证据分开记录。

## 先锁定上下文和状态矩阵

- 明确对象类型/ID、交易日期或时间窗、decision/event 版本、账户 scope、来源与返回锚点；换股、换日、切账户后，旧回包不得覆盖新状态，URL 不能充当权限。
- 分别验 follow 跟随、pin 固定、compare 对比：切换对象/日期时哪些视图应跟随、哪些应保持，草稿、滚动和焦点如何恢复。刷新、后退、旧链接及重开页面各验一次身份与别名迁移。
- 对每个资源区构造 loading、ready、有效空集、局部空、局部错误、stale、unknown；局部失败不把整页清零，错误不冒充无结果，陈旧数据显式显示源时点。
- 制造乱序响应、重复提交与取消/重试；只接受当前请求代次和同一对象/版本/scope 的结果。写动作显示 pending，后端确认后才显示成功，未知结果给查询或恢复路径。
- 检查键盘顺序、Overlay 初始/退出焦点、Esc、中文输入法组合期间 Enter、缩放/窄屏自然滚动；动画不得挡输入或焦点恢复，CSS 与 JS 减弱动效都验。

## 标准流程

```bash
# 1. 起浏览器并先加载一次页面（daemon 需要就绪）
agent-browser open "http://localhost:3000/page" >/dev/null 2>&1
agent-browser wait --load load >/dev/null 2>&1

# 2. 钉住目标接口（注意：先 unroute，见下方坑 1）
agent-browser network unroute >/dev/null 2>&1
agent-browser network route "**/backend/api/thing*" --body '{"data":{"items":[],"date":null}}' >/dev/null 2>&1

# 3. 探针确认桩真的生效（必做，否则后面看到的可能是旧渲染）
agent-browser eval "fetch('/backend/api/thing').then(r=>r.text()).then(t=>t.slice(0,80))"

# 4. 重新导航触发渲染，等待目标条件稳定再取快照
agent-browser open "http://localhost:3000/page" >/dev/null 2>&1
agent-browser wait --load load >/dev/null 2>&1
agent-browser snapshot 2>/dev/null > /tmp/snap.txt

# 5. 用 Grep 工具（不是 shell grep）检索分支文案，逐条对照

# 6. 收尾：清桩 + 关浏览器（无论如何都要执行）
agent-browser network unroute >/dev/null 2>&1
agent-browser close >/dev/null 2>&1
```

## 坑（都实测踩过）

1. **覆盖同类 pattern 必须先 `unroute` 再 `route`**：直接对同一 pattern 二次 `route` 时旧桩**静默残留**，
   快照拿到的还是上一份桩的渲染结果 —— 会误判成"新分支没生效"。每次换桩都先 `unroute`。
2. **桩生效必须用探针确认**：`eval "fetch(...).then(r=>r.text())"` 读回原文，
   确认是桩内容再继续。跳过这步等于在未知状态下做验收。
3. **`--body` 不支持 `@file`**：只能内联 JSON 字符串；中文直接写在单引号里可用。
4. **daemon 未就绪即 snapshot 会拿到空壳**（行数明显偏少的快照，如 40~60 行）。
   判断方法：快照行数远小于正常值 → 重跑 `open` + `wait` + `sleep 10`，不要分析空壳。
5. **`snapshot` 输出用 Grep 工具检索**，别用 shell `grep`（转义/glob 易出问题，且可能假阴性）。
6. **一个分支一份桩**：把"空列表"、"未生成"、"另一档"分别验一遍 —— 同一段 JSX 的不同入口
   （`items=[]` vs `date=null`）文案往往不同，只验一个会漏掉说谎的那句。

## 验收证据要求

- 每条分支都要给出实际渲染结果与对象/日期/版本/scope，记录桩、真实来源和请求顺序；不接受“应该会显示”。
- 涉及数量/口径的分支，同时核对**数字**（如"2 只"）与**口径注记**（如"入选门槛 综合分≥50"）都在。
- 若某条分支无法构造（缺依赖、需真实数据），如实说明"未验收"，不要写成已通过。

截图可证可见布局、遮挡与视觉层次，DOM/可访问性树可证文本、角色、焦点和交互目标；数值需对照同版响应与单位，源码只证实现路径。Canvas 像素不由 DOM 快照证明，转 canvas-chart-verify。某次宿主无法读取 PNG 仅限制该次目视证据，可换已获准的读图能力；不能据此断言所有模型永久无法看图。

## 与其他技能的分工

- Canvas 绘制内容（分时/K线/热力图）→ 用 `canvas-chart-verify`（挂点 + 像素采样）。
- 纯 DOM/文本渲染的条件分支 → 本技能。
