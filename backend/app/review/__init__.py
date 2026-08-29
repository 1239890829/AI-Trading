"""盘后复盘 Agent。

每个交易日收盘后自动运行，对当日操作与当日市场做全方位研判。

模块结构：
    config       方法论配置（可版本化，yaml 外置）
    schemas      核心数据结构（DataGap 是一等公民）
    models       持久化表（报告 / 改进项 / 元结论）
    collector    数据自采（缺失明确标注，不臆测）
    analyzers    分析器协议 + 规则实现 + LLM 占位
    model_router 模型路由（配置化切换 + 失败降级 + 成本记录）
    synthesis    改进项合成（含优先级与预期影响）
    methodology  元结论与方法论自我迭代
    storage      持久化 / 检索 / 历史对比
    service      编排 + 调度
"""
