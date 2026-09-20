---
name: ui-state-verify
description: 验收「只在罕见数据状态下才出现」的前端分支（空列表 / 未生成 / 分档变体 / 降级态）——用 agent-browser 的 network route 钉住接口响应，把罕见状态变成可控可重复的渲染场景，避免"等哪天自然出现"。适用于任何 SPA + REST 的 UI 分支验收。
read_when:
  - 新加/改动了空态、错误态、降级态、分档（A/B 档不同文案）等条件渲染分支
  - 该分支依赖的数据状态（列表为空、尚未生成、某标志位为 false）在当前环境里造不出来
  - 需要用「实际渲染」而不是读代码来证明分支正确
agent_created: true
---

# 罕见状态的 UI 分支验收（agent-browser network route 钉桩）

**问题**：条件渲染分支（`items.length === 0` / `data.date == null` / `strip_buy_range === false`）
只有在特定数据状态下才出现。等它在真实环境自然出现 = 无限期不验收；只读代码 = 用推理代替观察。

**解法**：把接口响应钉住（stub），把罕见的**数据状态**变成可控、可重复的**渲染场景**。

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

# 4. 重新导航触发渲染，等够时间再取快照
agent-browser open "http://localhost:3000/page" >/dev/null 2>&1
agent-browser wait --load load >/dev/null 2>&1
sleep 10
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

- 每条分支都要给出**实际渲染出的原文**（快照行号 + 文案），不接受"应该会显示"。
- 涉及数量/口径的分支，同时核对**数字**（如"2 只"）与**口径注记**（如"入选门槛 综合分≥50"）都在。
- 若某条分支无法构造（缺依赖、需真实数据），如实说明"未验收"，不要写成已通过。

## 与其他技能的分工

- Canvas 绘制内容（分时/K线/热力图）→ 用 `canvas-chart-verify`（挂点 + 像素采样）。
- 纯 DOM/文本渲染的条件分支 → 本技能。
