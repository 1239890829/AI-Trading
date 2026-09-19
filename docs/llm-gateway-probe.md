# LLM 网关健康探针（2026-09-06）

## 为什么要有它

网关（AgentRouter / `https://ps.air-outer.com`）出故障时，系统里只有**一种**表现：
「LLM 不可用 → 降级到 rules」。界面、复盘、摘要全都看不出原因。

但两类故障的处置**完全相反**：

| 分类 | 含义 | 处置 |
|---|---|---|
| `quota` | 网关账户余额不足（`403 need quota`） | **用户动作**：充值 |
| `gateway_error` / `timeout` | 网关侧失败或超时 | **运维动作**：等恢复，充钱没用 |

2026-09-04 全天复盘/摘要静默降级就是这么漏掉的，事后只能人肉翻日志。
本模块用最小调用定期体检，把失败落到分类上并外露。

## 排除掉的一个误判

`[claude-code:unrecognized_model]` 这类 stderr 提示**不能用来判断模型是否可用**。
2026-09-16 实测：`deepseek-v4-flash` 带同类提示仍约 3.3s 正常返回，而旧
`glm-5.3` 带同类提示却会挂起至超时；因此该文本与成败**正交**。当前 cc-switch
运行模型已切到 DeepSeek，2026-09-19 实测为 `deepseek-v4-flash`。故障分类必须看
returncode / timeout / stdout / HTTP 状态与 `LLMFailure`，**不得 match 该警告文本**。

## 失败分类（`app/core/llm_client.py`）

`LLMFailure` 枚举 + `LLMError.kind`，所有失败点都带分类，消费方按 `kind` 分流，
**不要 match 消息文本**（网关文案会变）。

`not_configured` / `unavailable` / `timeout` / `quota` / `gateway_error` /
`bad_response` / `empty` / `bad_request`

分类入口：
- `classify_text_failure(text)` —— 命中额度标记即 `quota`；刻意**不含裸 403**
  （网关 WAF 的 `401 unauthorized client detected` 是鉴权问题，不是额度）
- `classify_cli_failure(rc, stdout, stderr)` —— **stdout 也参与匹配**：CLI 把网关
  错误塞进 JSON 的 `result` 字段，只看 stderr 会漏判
- `classify_http_failure(status, body)` —— 402/429 归额度，其余非 2xx 归网关

## 探针（`app/services/llm_probe.py`）

- 状态机 `LlmProbe`，`probe_once(force=False, runner=None)`，`runner` 可注入做单测
- **成功长缓存（默认 30min）/ 失败短缓存（2min）**：省额度，但故障不会被永久掩盖
- 未配 model 或非 `claude_cli` 后端 → `state=disabled`，**绝不发请求**（不自己制造消耗）
- 探针失败不影响任何业务功能；异常全兜住落 `state`

## 端点

```bash
# 一站式可观测（只读上次结果，不触发调用）
curl -s 'http://127.0.0.1:8000/api/system/providers' | jq .llm_gateway

# 手动体检：force=1 发一次真实最小调用（约 $0.0006、1~8s，走 to_thread 不堵事件循环）
curl -s 'http://127.0.0.1:8000/api/system/llm-probe?force=1' | jq .
```

返回体关键字段：`state`（`ok`/`failed`/`disabled`/`idle`）、`last_failure_kind`、
`last_failure_hint`（人话提示，`quota` → "需给网关账户充值"）、`latency_ms`、
`consecutive_failures`、`cached`。

**`last_failure_kind == "quota"` 就是该充值了。**

## 配置（`.env`）

```
ASHARE_LLM_PROBE_ENABLED=true          # 关掉则不起后台循环
ASHARE_LLM_PROBE_INTERVAL_SECONDS=1800 # 半小时一拍；设 0 关闭（仍可手动触发）
```

## 与既有降级的关系

探针**只在旁路体检**，不改变任何调用路径：业务调用仍由 `ModelRouter`/`SummaryRouter`
兜底降级。两者共用同一套 `LLMFailure`。

## 助手浮窗文案（2026-09-06 已接线）

`POST /api/assistant/chat` 的 SSE `error` 事件现在带 `kind` 与 `hint`：

```json
{"type":"error","message":"need quota 0.098258","kind":"quota","hint":"网关额度不足，需给网关账户充值"}
```

前端 `floating-assistant.tsx` 优先显示 `hint`，技术原文降为次要行。实测渲染：

```
⚠️ 网关额度不足，需给网关账户充值

need quota 0.098258
```

过去只回一句英文报错，用户无从判断该做什么。

## 告警（2026-09-06 已接线）

连续失败达阈值 → 落 `AlertEvent` 并走 `NotifierRegistry` 分发，规则名
`__llm_gateway_probe__`（与 `__sentiment_monitor__` 同模式，get-or-create）。

```
ASHARE_LLM_PROBE_ALERT_AFTER=3              # 连续失败几次才发
ASHARE_LLM_PROBE_ALERT_COOLDOWN_SECONDS=3600 # 冷却：定时探针不冷却必然刷屏
ASHARE_LLM_PROBE_CHANNELS=in_app,log,feishu
```

- 冷却是硬要求（2026-09-04 定案的"预警规则无冷却"缺陷：3.5h 连发 7 条雷同事件）
- `disabled`（未配模型）不算故障，不发告警
- `fire_llm_alert` 内部兜住所有异常——告警是旁路的旁路，绝不能拖垮探针

端到端实测（2026-09-06）：规则 4 / 事件落库 / 日志输出
`[LLM 网关] 连续 1 次体检失败：网关额度不足，需给网关账户充值（kind=quota）`。
