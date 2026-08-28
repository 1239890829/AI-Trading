# 数据源说明

## 0. 接入策略（§2.2 主源→备源→降级）

`ASHARE_DATA_PROVIDER` 主源 + `ASHARE_PROVIDER_FALLBACKS` 备源，由 `CompositeProvider` 逐方法自动切换：

```text
行情/K线/盘口/搜索: 腾讯 → 东财（search suggest / K线）
涨停池 / 龙虎榜:    东财 push2ex / datacenter（稳定，独立主机）
逐笔成交:           东财 details（push2his，本机被限流时该能力 502）
默认链:             chain(tencent→eastmoney)
```

- 全部 Provider 客户端 `trust_env=False`：行情源为国内站直连，**不走用户系统代理**（SOCKS 代理会导致 httpx 启动失败或绕道境外）。
- 切换日志写入 `CompositeProvider.switch_log`，日志输出 `provider switched`。
- 全链失败 → QuoteHub 标记 `stale`，health 报 `degraded`，**不伪造实时数据**。

## 1. 腾讯（主源，实测 2026-08-28，Level-1 快照 3 秒级）

| 用途 | 端点 | 口径 |
|---|---|---|
| 实时快照/五档 | `qt.gtimg.cn/q=sh600519,...` (GBK, `~` 分隔) | 字段(0起): 1名 3现价 4昨收 5开 6量(手) **9-18买五档价量 19-28卖五档价量** 30时间(北京) 31涨跌 32涨跌% 33高 34低 36量(手) 37额(**万**) 38换手% |
| 日/周 K 线 | `web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600519,day,,,320,qfq` | `data.sh600519.qfqday` 行=`[日期,开,收,高,低,量(手)]`，qfq=前复权 |
| 分钟 K 线 | `ifzq.gtimg.cn/appstock/app/kline/mkline?param=sh600519,m5,,320` | `[YYYYMMDDHHMM,开,收,高,低,量]`，m1~m60 |
| 搜索 | `smartbox.gtimg.cn/s3/?v=2&q=...&t=all` (GBK) | `^`分组 `~`字段：名称/代码(sh600519) |

单位：成交量手（×100 转股），成交额万（×1e4 转元）——已用茅台盘后数据双重验证
（16126 手×100 ≈ 20.86 亿÷1297.40 ✓；指数 9703.65 亿与东财 f6 一致 ✓）。
时间戳为北京时间，Normalizer 统一转 UTC。

## 2. 新浪（备源）

`hq.sinajs.cn/list=sh600519,...`（GBK，必须带 `Referer: https://finance.sina.com.cn/`）。
字段：0名 1开 2昨收 3现价 4高 5低 6买一价 7卖一价 8量(**股**) 9额(元) 10-19买五档 20-29卖五档 30日期 31时间。
提供快照 + 五档盘口；K 线未实现（由腾讯/东财兜底）。

## 3. 东方财富（专项源）

- **涨停池** `push2ex.eastmoney.com/getTopicZTPool`：p=价格×100、fbt/lbt=HHMMSS、fund=封单额(元)、zbc=炸板、lbc=连板、zttj={days,ct}（"7天7板"）。实测 2026-08-28：82 只。
- **龙虎榜** `datacenter-web.eastmoney.com/api/data/v1/get` (RPT_DAILYBILLBOARD_DETAILSNEW)：SECURITY_CODE / BILLBOARD_* / **EXPLAIN**(上榜原因)。
- 行情族 `push2/push2his`：本机直连与经代理均被 WAF 拦（空回复，疑似共享出口 IP 风控），保留为链上 search/K线/逐笔的备源；家庭宽带通常可用。
- `ulist` 与 `stock/get` 字段编号**不一致**，不可混用映射表。

## 4. 同花顺（分时候选，已验证可达）

`d.10jqka.com.cn/v6/line/hs_600519/01/today.js`（JSONP）返回当日分时：开/高/低/现价/量/额/均价等。
社区项目常用其分时与增量 K 线（`/v6/line/hs_600519/01/2026.js`）。Phase 3 做**分时图**时优先接入，
当前未实现 Provider（保持接口契约，见 docs/api.md）。

## 5. Mock（演示/测试专用）

确定性种子数据，`source="mock"`、`realtime=False`，永不冒充实盘。只能单独使用
（`ASHARE_DATA_PROVIDER=mock`），禁止混入真实源链。

## 6. 社区参考（GitHub 调研 2026-08-28）

- [easyquotation](https://github.com/shidenggui/easyquotation)（5.4k★）：新浪/腾讯实时快照——与本库选型一致。
- [akshare](https://github.com/akfamily/akshare)：聚合新浪/东财/腾讯，全品类数据（Phase 4 基本面接入候选）。
- 同类聚合库普遍采用「腾讯/新浪快照 + 东财数据网」组合；腾讯字段更全（知乎测评）。

## 7. 数据入库必带字段

`created_at / updated_at / data_timestamp / received_at / source / quality / version`（§3.3）；
quality ∈ high/medium/low/stale/invalid，低质量数据 AI 禁用、回测禁用、前端强制标识。
