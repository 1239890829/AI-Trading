"""OpenAI Agents API 探针（P2-38 附属，§6.25 评估的「实跑」部分——等 key 充值后一键执行）。

运行环境：backend/.venv-research（已装 openai-agents 0.22.2）。
密钥纪律：只读环境变量 OPENAI_API_KEY，**绝不硬编码/落库/入文档**（红线 4）。
key 获取：platform.openai.com → API keys；充值：billing → add credits（$5 起足够实验）。

三个探针（对应 2026-09-13 评估的三个考点）：
  ① Responses + web_search：消息面兜底潜力（问 09-11 PCB 大涨驱动，看 A 股内容质量与时效）
  ② Agents SDK + function tool：**调我们自己的 marketdb**（真实集成形态——agent 取茅台
     最新收盘并解读，验证「外部自定义工具」链路与 hallucination 程度）
  ③ handoffs 双 agent：简单编排（数据 agent → 解读 agent），评估多 agent 胶水层价值

用法：
    OPENAI_API_KEY=sk-xxx .venv-research/bin/python scripts/openai_agents_probe.py [--probe 1|2|3|all]
默认 all；无 key/无余额 → 显式退出并指引（不静默）。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402

MARKETDB = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"


def _key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        print("❌ 未设置 OPENAI_API_KEY（红线：key 只走环境变量/backend/.env，不入库）")
        sys.exit(1)
    return key


def probe_models() -> None:
    import urllib.request

    req = urllib.request.Request("https://api.openai.com/v1/models",
                                 headers={"Authorization": f"Bearer {_key()}"})
    import json

    d = json.load(urllib.request.urlopen(req, timeout=30))
    models = [m["id"] for m in d.get("data", [])]
    print(f"① key 有效 · {len(models)} 模型 · gpt-5 系: "
          f"{[m for m in models if m.startswith('gpt-5')][:5]}")


def probe_web_search() -> None:
    """考点①：web_search 对 A 股题材消息的覆盖质量（消息面兜底候选能力）。"""
    import json
    import urllib.error
    import urllib.request

    body = {
        "model": "gpt-5-nano",
        "tools": [{"type": "web_search"}],
        "input": "2026年9月11日A股PCB板块大涨的驱动消息是什么？给我当天相关的2-3条具体新闻标题和来源",
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=120))
    except urllib.error.HTTPError as e:
        hint = {402: "无余额 → platform.openai.com/settings/organization/billing 充值（$5 起）",
                429: "限流 → 无余额账户速率极低，充值后重试"}.get(e.code, f"HTTP {e.code}")
        print(f"❌ {hint}")
        return
    if "error" in d:
        print(f"❌ {d['error'].get('message', '')[:200]}（无余额 → billing 充值）")
        return
    print(f"② web_search 跑通 · 用量 {d.get('usage', {})}")
    for item in d.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    print("  回答:", c.get("text", "")[:400])


def _mt_close(symbol: str) -> str:
    """function tool 的真实后端：marketdb 茅台最近 3 根收盘（我们自己的数据域）。"""
    con = duckdb.connect(str(MARKETDB), read_only=True)
    try:
        rows = con.execute(
            "select date_ms, close_price from daily_k where thscode = ? "
            "order by date_ms desc limit 3", [f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ"]
        ).fetchall()
    finally:
        con.close()
    return "; ".join(f"{pd_date(ms)}: {px}" for ms, px in rows)


def pd_date(ms: int) -> str:
    from datetime import datetime, timezone, timedelta

    return (datetime.fromtimestamp(ms / 1000, tz=timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")


async def probe_agent_tool() -> None:
    """考点②：Agents SDK + function tool 调 marketdb（外部自定义工具链路）。"""
    from agents import Agent, Runner, function_tool

    @function_tool
    def get_recent_close(symbol: str) -> str:
        """返回指定 A 股代码最近 3 个交易日的收盘价（来源：本项目 marketdb）。"""
        return _mt_close(symbol)

    agent = Agent(name="行情助手", model="gpt-5-nano",
                  instructions="你是 A 股数据助手。需要价格时必须调用工具 get_recent_close，不得编造。"
                               "回答附上数据日期。")
    result = await Runner.run(agent, "贵州茅台最近三天收盘价是多少？用一句话解读趋势。")
    print("③ Agents SDK + function tool 跑通：")
    print("  ", result.final_output[:300])


async def probe_handoff() -> None:
    """考点③：handoffs 双 agent 编排（数据员 → 分析员的胶水层形态）。"""
    from agents import Agent, Runner, function_tool

    @function_tool
    def get_recent_close(symbol: str) -> str:
        """返回指定 A 股代码最近 3 个交易日的收盘价。"""
        return _mt_close(symbol)

    data_agent = Agent(name="数据员", model="gpt-5-nano",
                       instructions="只负责调用工具取数并原样转述，不做解读。",
                       tools=[get_recent_close])
    analyst = Agent(name="分析员", model="gpt-5-nano",
                    instructions="你收到数据员的转述，用一句话给出趋势判断并注明数据日期。"
                                 "需要更多数据时 handoff 回数据员。", handoffs=[data_agent])
    result = await Runner.run(analyst, "请分析贵州茅台（600519）近期走势。")
    print("④ handoffs 编排跑通：")
    print("  ", result.final_output[:300])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", default="all", choices=["1", "2", "3", "4", "all"])
    args = ap.parse_args()
    _key()
    if args.probe in ("1", "all"):
        probe_models()
    if args.probe in ("2", "all"):
        probe_web_search()
    if args.probe in ("3", "all"):
        asyncio.run(probe_agent_tool())
    if args.probe in ("4", "all"):
        asyncio.run(probe_handoff())


if __name__ == "__main__":
    main()
