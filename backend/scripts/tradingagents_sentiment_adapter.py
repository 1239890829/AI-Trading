"""Narrow nullable sentiment adapter for a pinned, complete TradingAgents graph.

The caller supplies eligibility from frozen sources. No provider, source fetch,
node replacement, renderer rewrite for scored reports, or score imputation.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Optional

from pydantic import BaseModel, create_model, model_validator
from pydantic_core import PydanticUndefined


NEWS_HEADING = '### News headlines — Yahoo Finance, past 7 days'
BAND_INSTRUCTION = ('- **overall_band**: Exactly one of Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. '
                    'Use Mixed when sources point in clearly different directions; Neutral only when all sources are genuinely silent.')
SCORE_INSTRUCTION = ('- **overall_score**: A number from 0 (maximally bearish) to 10 (maximally bullish); '
                     '5 is neutral. Keep it consistent with overall_band.')
DEFAULT_NEWS_LABEL = 'Frozen A-share source records'


def make_sentiment_adapter(base_report, native_render, has_evidence):
    """Return (derived_schema, renderer), retaining the base and all its fields.

    has_evidence is a frozen caller decision about eligible score evidence, not a
    model assertion or a search for positive words. False forbids any numeric
    score/band. Paired null is permitted as an explicit abstention, never 0/5.
    Original enum values, 0..10 numeric constraints and other fields survive.
    """
    if not isinstance(base_report,type) or not issubclass(base_report,BaseModel):
        raise TypeError('Original Pydantic report class required')
    if not callable(native_render) or not isinstance(has_evidence,bool):
        raise TypeError('Original renderer and explicit frozen evidence boolean required')
    if not {'overall_band','overall_score','confidence','narrative'}.issubset(base_report.model_fields):
        raise ValueError('Original sentiment fields missing')

    def validate_pair(report):
        band,score=report.overall_band,report.overall_score
        if (band is None)!=(score is None):
            raise ValueError('Sentiment band and score must be provided or null together')
        if not has_evidence and (band is not None or score is not None):
            raise ValueError('No eligible evidence: sentiment band and score must both be null')
        if not isinstance(report.narrative,str) or not report.narrative.strip():
            raise ValueError('Nonempty sentiment explanation remains required')
        return report

    overrides={}
    for name in ('overall_band','overall_score'):
        info=deepcopy(base_report.model_fields[name])
        annotation=Optional[info.annotation]
        info.annotation=annotation
        info.default=PydanticUndefined
        info.default_factory=None
        description=info.description or ''
        if name=='overall_band':
            description=description.replace(
                'Use Neutral only when all sources are genuinely silent or non-committal.',
                'Use Neutral for supported non-committal evidence, not missing evidence.')
        info.description=description+' Band and score must be null together when eligible evidence is missing; null means unknown, not neutral.'
        overrides[name]=(annotation,info)
    derived=create_model(base_report.__name__+'EvidenceAware',__base__=base_report,
                         __validators__={'_validate_evidence_pair':model_validator(mode='after')(validate_pair)},**overrides)

    def render(report):
        if not isinstance(report,derived):
            report=derived.model_validate(report.model_dump() if isinstance(report,BaseModel) else report)
        if report.overall_score is not None:
            return native_render(report)
        return '\n'.join(['**Overall Sentiment:** **Unknown** (Score: 未提供分数)',
                          f'**Confidence:** {report.confidence.capitalize()}','',report.narrative])
    return derived,render


def adapt_sentiment_system_message(original_message, *, has_evidence, news_source_label=DEFAULT_NEWS_LABEL):
    """Replace only three exact anchors in the original _build_system_message.

    Every other character (source blocks, duties, ordering and language suffix)
    is retained. Unknown upstream wording fails closed rather than rewriting it.
    """
    if not isinstance(original_message,str) or not isinstance(has_evidence,bool):
        raise TypeError('Original prompt text and evidence boolean required')
    if (not isinstance(news_source_label,str) or not 1<=len(news_source_label)<=160
        or any(c in news_source_label for c in '\n\r<>')):
        raise ValueError('Static single-line source label required')
    for anchor in (NEWS_HEADING,BAND_INSTRUCTION,SCORE_INSTRUCTION):
        if original_message.count(anchor)!=1:
            raise ValueError('Pinned sentiment prompt anchor missing or repeated')
    condition=('No eligible score evidence is supplied in this frozen window: both fields must be null.'
               if not has_evidence else
               'Eligible frozen score evidence is supplied; use it, or explicitly abstain with both fields null and explain why.')
    band=('- **overall_band**: Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish when supported by eligible evidence. '
          'Use Mixed for genuine conflicting evidence; missing evidence is Unknown, not Neutral. '
          'overall_band and overall_score must be null together for an unknown assessment. '+condition)
    score=('- **overall_score**: A number from 0 (maximally bearish) to 10 (maximally bullish) only with eligible evidence; '
           '5 means supported neutral evidence. Keep it consistent with overall_band. '
           'For missing evidence provide null, never 0 or 5 as a substitute; the renderer will show Unknown / 未提供分数.')
    return (original_message.replace(NEWS_HEADING,'### News headlines — '+news_source_label+', past 7 days')
            .replace(BAND_INSTRUCTION,band).replace(SCORE_INSTRUCTION,score))
