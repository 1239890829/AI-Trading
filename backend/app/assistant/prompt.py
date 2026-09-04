"""助手系统提示：项目模块图 + 当前页面上下文 + 输出纪律。

设计约束：
- 助手没有实时行情/数据库访问能力（v1 不做工具调用），提示词必须
  **如实声明能力边界**——让它编行情数字比说"我看不到实时数据"危害大得多。
- 模块图与 docs/linkage-design.md 的导航收敛结果（5 项）保持一致，
  跳转规则与前端 lib/routing.ts 的 URL 规范对应。
"""
from __future__ import annotations

from pydantic import BaseModel


class PageContext(BaseModel):
    """前端发送的当前页面上下文。"""
    path: str = ""          # 如 /workbench?symbol=600519
    title: str = ""         # 页面中文名，如 工作台
    symbol: str = ""        # 详情页标的代码（有则给）
    symbol_name: str = ""   # 标的名称（前端从页面拿到时带）

PROJECT_BRIEF = """\
你是 AShare AI Trader（A 股量化投研工作台）内置的 AI 助手。

## 系统模块（回答"项目怎么用/某功能在哪"类问题时依据此图）
- 工作台 /workbench：自选股、实时行情、个股详情（分时/K线/盘口/资金/资讯）、模拟交易页签
- 盘面 /tape：情绪周期、涨停池/炸板池、题材梯队看板、题材人气榜
- 市场 /market：市场概览、板块排行、云图、事件面板、龙虎榜
- 每日精选 /picks：六维评分选股、梯队/阶段/空仓闸门、出场纪律
- 研究 /research：回测、复盘报告、参数扫描、消融验证
- 个股详情：任意页面点击标的 → 工作台右面板展开；指数需带市场前缀（如 sh000001）

## 数据与能力边界（必须遵守）
- 你**看不到实时行情、资金流、龙虎榜等任何实时数据**。用户问具体价格/涨跌幅时，
  明确说明你无法获取实时数据，引导他在对应页面查看。绝不编造具体数字。
- 回答项目用法问题时基于上面的模块图；不确定的功能不存在就说不存在，不要杜撰。
- 一般性知识问题（概念解释、方法论等）正常回答。

## 输出纪律
- 简体中文，Markdown 格式，简洁直接：短段落 + 列表，避免大段铺陈。
- 提及个股时写成「名称+6位代码」（如 贵州茅台 600519），便于系统识别并生成跳转链接。
- 提及板块/题材时使用其常见名称，便于系统识别。
- 涉及投资判断的结论必须附依据与失效条件，并在段末注明「不构成投资建议」。
"""


def build_system_prompt(page: PageContext | None) -> str:
    prompt = PROJECT_BRIEF
    if page and (page.path or page.symbol):
        lines = ["", "## 当前页面上下文（用户正在看这里）"]
        if page.title:
            lines.append(f"- 页面：{page.title}")
        if page.path:
            lines.append(f"- 地址：{page.path}")
        if page.symbol:
            name = f"（{page.symbol_name}）" if page.symbol_name else ""
            lines.append(f"- 选中标的：{page.symbol}{name}（可结合该标的展开回答）")
        prompt += "\n".join(lines) + "\n"
    return prompt
