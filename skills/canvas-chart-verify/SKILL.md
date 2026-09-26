---
name: canvas-chart-verify
description: Canvas 图表改动后的实际渲染验收；按宿主能力结合截图目视、DOM、实例挂点、像素与同版数值，核对象日期版本账户范围、交互、乱序回包和恢复。
---

# Canvas 图表验收工作流（agent-browser 文本通道 + 像素采样）

适用于分时、K线、热力图的区间、priceLine、颜色、主题切换和实例稳定性验证；根据宿主可用能力选择工具。

**背景**：Canvas 内部通常不进入 DOM/无障碍快照。某次宿主若无法读取 PNG，只表示该次没有目视证据；具备获准读图能力时应直接检查截图。挂点、像素与数据核对可补足不同证据，不能把源码或单个颜色命中冒充画面与金融语义正确。

验收前固定对象类型/ID、交易日期/时间窗、数据源时点、解释/决策版本和账户 scope。切股/切日/切账户、follow/pin/compare、乱序回包、局部空错陈旧、旧链接返回及关闭 Overlay 后的焦点/滚动恢复要逐项核；历史图不能混入未来数据，价格/百分比/成本/成交/复权单位不能混用。

证据分层：截图目视证布局、遮挡、颜色和可读性；DOM 证容器、文本、焦点与交互；挂点证实例身份、序列和计算区间；像素证指定图层确实绘制；同版数值对照证金融含义。换主题后还要核实例未意外重建、真实帧时间/内存和 CSS/JS 减弱动效，不以动画更丰富推断性能更快。

## 0. 选择当前已获准的浏览器工具

```bash
agent-browser open "http://localhost:3000/..."   # 仅在已安装且获准使用时
```

- 下方命令以 agent-browser 为实例；不存在时使用当前宿主已提供的浏览器/读图工具，不为验收自动安装新依赖。结束时清理本次桩与浏览器会话。
- 页面加载：`open` 后 `sleep 5~6`（SPA 数据就绪），不要依赖 `wait --load networkidle`（可能挂死）。

## 1. 组件挂调试挂点（改组件时顺手加，验收/调试长期受益）

Canvas 无 DOM 文本可读，在图表组件的创建 effect 里把实例与关键状态挂到容器元素：

```ts
const dbg = ref.current as unknown as { __myChart?: IChartApi; __myRange?: unknown };
dbg.__myChart = chart;
dbg.__myRange = { min, max };   // 想验收的派生状态（如纵轴区间）
// cleanup 里 delete dbg.__myChart; delete dbg.__myRange;
```

页面 eval 直读（遍历 div 找挂点，勿猜具体容器）：

```js
[...document.querySelectorAll('div')].filter(d => d.__myChart)
  .map(d => ({ range: d.__myRange, margins: d.__myChart.priceScale('right').options().scaleMargins }))
```

- lightweight-charts 4.x：`chart.priceScale('right').options()` 可读 margins 等配置；
  `IPriceScaleApi` **没有** getVisibleRange（那是 timeScale 的）——纵轴区间靠自己挂 `__myRange`。

## 2. canvas 像素采样（验证线/刻度真的画出来了）

**坑 1：每层有两个同尺寸 canvas 副本**（主绘制 + crosshair overlay）。
采样文字墨迹必须取**每尺寸的第一副本**；overlay 副本采样恒为 0，会误判"没画"。

**坑 2：轴宽度不固定**（不同页面 56/64/68/70px）——按 `height === pane.height && width < 100`
动态筛，别写死。

**坑 3：透明度混合后的颜色阈值要放宽**。`rgba(239,68,68,0.55)` 画在深色背景上
混合后 r≈142 而非 239。用色相差判别（r>110 且 r-g>40 且 r-b>40），别用 `r>200`。

**坑 4：canvas 尺寸是设备像素**（CSS × DPR），贴边位置按设备像素算：
margins top 0.02 × 高 286 ≈ y=6 处应有横线。

```js
const d = [...document.querySelectorAll('div')].find(x => x.__myChart);
const cs = [...d.querySelectorAll('canvas')];
const pane = cs.reduce((a,b) => b.width*b.height > a.width*a.height ? b : a);
const ctx = pane.getContext('2d');
// 行扫描找横线（如涨停虚线）：每行统计红色像素列数，虚线 row>5 即命中
const hits = [];
for (let y = 2; y < pane.height*0.1; y++) {
  let row = 0;
  for (let x = pane.width*0.3; x < pane.width*0.75; x += 2) {
    const p = ctx.getImageData(x, y, 1, 1).data;
    if (p[0] > 110 && p[0]-p[1] > 40 && p[0]-p[2] > 40) row++;
  }
  if (row > 5) hits.push({y, row});
}
// 刻度墨迹（轴 canvas 上有没有文字）：alpha>50 且亮度>200 的像素计数
```

常用颜色判别式：红 `r>110 && r-g>40 && r-b>40`；绿 `g>90 && g-r>30 && g-b>20`；
黄（#eab308）`r>150 && g>110 && b<110 && r-b>60`。

## 3. 页面交互（React 受控组件）

- 点击 tab/按钮用 eval 原生 `.click()`：`[...document.querySelectorAll('button')].find(b => b.textContent.trim().startsWith('分时'))?.click()`——agent-browser 自带 `click @ref` 会静默失灵（返回 ✓ 但处理器不触发）。
- 匹配文本用 `startsWith` 而非全等：按钮内常带计数 span（"每日精选5"）。
- 点击后等 2~3s 再读 UI（数据/渲染异步）。
- 详情页真实路由是 `/workbench?symbol=300750`（/stock/xxx 只是中转重定向）。
- 默认图表 tab 可能是 K 线，验证分时图要先点「分时」。

## 4. 诚实汇报

- 像素采样只能证明「有某种颜色的东西画在某个位置」，不能证明语义正确——
  语义正确性靠挂点状态（区间数值）+ 单测覆盖判定函数，两者结合才是完整验收。
- 若当前宿主只能读文本而无截图/读图能力，布局与间距标为未目视；可用获准读图工具时直接核图，不让用户替代可自行完成的验收。

## 5. 主题/配色类改动的验收（P2-26 沉淀，2026-09-11）

**这类验收最容易做假**：像素「最高频色」在不同调色板下会**合并/拆分颜色桶**，直接比计数会得出错误结论。
实例：暗档 `a1a1aa` 同时是「轴文字」与「昨收虚线」，亮档拆成 `71717a`(线) + `52525b`(文字)
⇒ 计数 401 vs 398 看着"一致"，其实是巧合。**四件套缺一件就有一类结论无法成立**：

1. **实例戳 —— 证明「只换色、不重建」**（最直接，比像素强）
   在容器 div 与 `IChartApi` 实例上各打 token，切主题后校验两者仍在：
   ```js
   d.__divToken = 'DIV-' + Date.now();
   d.__minuteChart.__chartToken = 'CHART-' + Date.now();
   ```
   无挂点的组件替代做法：`window.__kc = [...document.querySelectorAll('canvas')]`，
   切换后校验 `window.__kc.every(c => c.isConnected)` —— 重建会让旧 canvas 脱离文档。
2. **颜色无关的几何不变量 —— 证明「几何没动」**
   统计某区域的**墨迹像素数（alpha>8）**，与颜色无关 ⇒ 两档必须相等（可差 1~2 px 的 AA 量化）。
   实测：量能副图区 `4112/4112/4112`、整 pane `12662/12660/12662`。
   区域要**只含目标元素**（量能区取底部 20%：只有量柱+网格，价格线/均线/填充都不在此区）。
3. **逐色槽容差统计 —— 证明「颜色真的换档」**
   每个 palette 槽位给**暗/亮两个候选色**，各自统计 ±6 容差内像素数，输出 `dark=N light=M`。
   合格形态：暗档 `dark=2482 light=0`、亮档 `dark=0 light=2656` ⇒ 1:1 换档、无残留；
   同时能抓出「只改了一半」（某槽两档都非 0 或恒为 0）。
4. **三态（暗→亮→暗）—— 排除「只是偶然重画了一次」**
   回切后必须**逐字节还原**（`ink` 与各槽计数与首次完全一致）才说明换色是幂等的。
   ⚠️ 图表有实时数据（WS）时末根 bar 会动，计数随之微变 —— 先说明是数据在动，别当成回退。

### 坑 5：桩必须满足**应用的 API 信封契约**（本轮实测踩到）

给分时图钉桩（`ui-state-verify` 手法）时，桩返回**平铺对象** `{symbol,points,...}`，
而本项目 `request()` 强制校验 Envelope 的 `data` 键，缺失即抛 `bad_envelope`；
调用方 `.catch(() => {})` 把错吞掉 ⇒ **页面显示"暂无数据"，但探针却"通过"**——
因为探针直接 `j.points.length`，**绕过了应用自己的解析层**。

- 探针必须**走应用会走的路径**，或至少校验应用会校验的契约（信封 / 字段名 / 类型）。
- 本例正确探针（同时打印顶层键，一眼看出信封在不在）：
  ```js
  fetch(u).then(r => r.json()).then(j => ({ top: Object.keys(j), n: (j.data && j.data.points || []).length }))
  ```
- `agent-browser network route` 只支持 `--body` / `--abort`，**不能设响应头** ⇒ 无法靠 content-type 兜底；
  先 curl 真实端点核对顶层结构（本项目的形态是 `{data, meta}`）再套信封。
- 排查顺序（本轮实战有效）：`network requests --filter <path>` 看**应用实际请求了哪个 URL**
  （曾据此发现面板取的是 600519 而非 URL 上的 300750）→ 给 `window.fetch` 打临时探针读回**应用真正收到的点位数**。
