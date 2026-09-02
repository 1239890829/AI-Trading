"""LLM 摘要器：规则底稿 + LLM 摘要增强。

与 `app/review/analyzers.py::LLMAnalyzer` 同一套原则——**LLM 不直接造数据**：
1. `RulesSummarizer` 先跑出确定性底稿（重要度/情绪/摘要/关键数字）
2. LLM 基于原文重写每条的重要度、情绪与事实摘要；`numbers` 保留规则层
   的正则抽取结果（确定性，LLM 抄数字反而容易错）
3. 字段校验不过（重要度/情绪取值非法、digest 非字符串）的单条保留规则原判
4. LLM 回复里**没有任何一条可应用**时整体上抛 → SummaryRouter 降级
   规则摘要器——否则"来源标着 LLM、内容全是规则"就是又一类静默失真
5. 被 LLM 改写的条目 `digest_source="LLM"`，来源可追溯

需要配置 `ASHARE_NEWS_LLM_BASE_URL` / `ASHARE_NEWS_LLM_API_KEY` /
`ASHARE_NEWS_LLM_MODEL`（OpenAI 兼容接口）后可用；未配置时
SummaryRouter 自动降级到规则摘要器。
"""
from __future__ import annotations

import json
import logging

from app.news.rules import RulesSummarizer

log = logging.getLogger(__name__)

_LLM_SYSTEM_PROMPT = """\
你是 A 股新闻/公告摘要增强层。输入是带 id 的条目列表（title/summary/type 为原文）。

要求：
1. 只输出一个 JSON 对象，格式：
   {"items": [{"id": "news:0", "importance": "高|中|普通|低", "importance_score": 整数,
   "importance_reasons": ["判定依据"], "sentiment": "偏正面|偏负面|分歧|中性",
   "sentiment_reasons": ["命中依据"], "digest": "一句话事实摘要"}]}
2. digest 只能复述原文已有的事实，禁止引入原文没有的信息、禁止推断与建议，
   不超过 90 字；原文为表格数据时概括标题即可。
3. importance_score 参考：监管/退市风险与定期业绩 40 上下，资本运作 35 上下，
   再融资/股东行为 20-25，例行事项（说明会/权益分派实施）为负。
4. 每条输入都必须返回，id 原样带回。\
"""

_VALID_IMPORTANCE = {"高", "中", "普通", "低"}
_VALID_SENTIMENT = {"偏正面", "偏负面", "分歧", "中性"}
_DIGEST_MAX = 160  # LLM 偶尔不听话写长，硬截断兜底（规则层是 90）


class LLMSummarizer:
    name = "llm"

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        client=None,  # httpx.Client，供测试注入 MockTransport
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model or "unknown"
        self._client = client

    def is_available(self) -> bool:
        return bool(self.base_url and self.api_key)

    def summarize(self, news: list[dict], announcements: list[dict]) -> dict:
        from app.core.llm_client import chat_completion, extract_json_object

        # 1. 规则底稿：结构完整、必然可用，LLM 是在其上做增强
        result = RulesSummarizer().summarize(news, announcements)

        # 2. 构造输入：带稳定 id（kind:序号），LLM 按 id 回填
        items_in: list[dict] = []
        for kind in ("news", "announcements"):
            for i, it in enumerate(result[kind]):
                summary = (it.get("summary") or "").strip()
                items_in.append({
                    "id": f"{kind}:{i}",
                    "kind": kind,
                    "title": it.get("title"),
                    "type": it.get("type") if kind == "announcement" else None,
                    "summary": summary[:400] or None,
                })
        if not items_in:
            return result  # 没有可摘要的条目，不必惊动 LLM

        messages = [
            {"role": "system", "content": _LLM_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps({"items": items_in}, ensure_ascii=False),
            },
        ]
        content = chat_completion(
            self.base_url, self.api_key, self.model, messages, client=self._client,
        )

        # 3. 解析 + 逐条校验回填
        payload = extract_json_object(content)
        raw = payload.get("items")
        if not isinstance(raw, list):
            raise ValueError("LLM 回复缺少 items 数组")
        by_id: dict[str, dict] = {}
        for row in raw:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                by_id[row["id"]] = row

        applied = 0
        unknown_ids = []
        for kind in ("news", "announcements"):
            for i, it in enumerate(result[kind]):
                row = by_id.pop(f"{kind}:{i}", None)
                if row is None:
                    continue
                patch = self._valid_patch(row)
                if patch:
                    result[kind][i] = {**it, **patch}
                    applied += 1
        unknown_ids = sorted(by_id)
        if unknown_ids:
            log.warning("LLM 回复含未知条目 id，已丢弃：%s", unknown_ids)

        if applied == 0:
            # 一条都没采用 = LLM 输出整体不可信，上抛让路由层显式降级，
            # 绝不带着 "actual=llm" 的标签返回规则内容
            raise ValueError("LLM 回复没有任何可应用的条目")

        # 4. 重要度可能被 LLM 调整过 → 重新排序（与规则层同键）
        for kind in ("news", "announcements"):
            result[kind].sort(
                key=lambda r: (r["importance_score"], r.get("date") or ""),
                reverse=True,
            )
        return result

    @staticmethod
    def _valid_patch(row: dict) -> dict:
        """从 LLM 单条回复中挑出合法字段；非法字段一律不采用。"""
        patch: dict = {}
        if row.get("importance") in _VALID_IMPORTANCE:
            patch["importance"] = row["importance"]
        score = row.get("importance_score")
        if isinstance(score, bool):
            score = None
        elif isinstance(score, float) and score.is_integer():
            score = int(score)
        if isinstance(score, int):
            patch["importance_score"] = score
        if row.get("sentiment") in _VALID_SENTIMENT:
            patch["sentiment"] = row["sentiment"]
        for field in ("importance_reasons", "sentiment_reasons"):
            vals = row.get(field)
            if isinstance(vals, list):
                patch[field] = [v for v in vals if isinstance(v, str) and v.strip()]
        digest = row.get("digest")
        if isinstance(digest, str) and digest.strip():
            patch["digest"] = digest.strip()[:_DIGEST_MAX]
            patch["digest_source"] = "LLM"
        return patch
