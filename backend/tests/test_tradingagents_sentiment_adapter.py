"""Constraint-equivalent small schema; no external TradingAgents dependency."""
from enum import StrEnum
import importlib.util
from pathlib import Path
from typing import Literal
import unittest

from pydantic import BaseModel, Field, ValidationError

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/tradingagents_sentiment_adapter.py'
SPEC=importlib.util.spec_from_file_location('sentiment_adapter_test',SCRIPT)
A=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(A)


class Band(StrEnum):
    BULLISH='Bullish'
    NEUTRAL='Neutral'
    MIXED='Mixed'


class SmallReport(BaseModel):
    overall_band: Band=Field(description='Original sentiment direction.')
    overall_score: float=Field(ge=0,le=10,description='Original 0..10 score.')
    confidence: Literal['low','medium','high']
    narrative: str


def native_render(report):
    return '\n'.join([f'**Overall Sentiment:** **{report.overall_band.value}** (Score: {report.overall_score:.1f}/10)',
                      f'**Confidence:** {report.confidence.capitalize()}','',report.narrative])


def body(**changes):
    return dict(overall_band='Bullish',overall_score=7,confidence='low',narrative='合格新闻的主观语气判断。',**changes)


class SentimentAdapterTests(unittest.TestCase):
    def test_positive_renderer_and_original_schema_are_unchanged(self):
        before=SmallReport.model_json_schema()
        derived,render=A.make_sentiment_adapter(SmallReport,native_render,True)
        report=derived.model_validate(body())
        self.assertIsInstance(report,SmallReport)
        self.assertEqual(render(report),native_render(SmallReport.model_validate(body())))
        self.assertEqual(set(derived.model_fields),set(SmallReport.model_fields))
        self.assertEqual(SmallReport.model_json_schema(),before)

    def test_both_null_render_unknown_without_fake_neutral_score(self):
        derived,render=A.make_sentiment_adapter(SmallReport,native_render,False)
        report=derived(overall_band=None,overall_score=None,confidence='low',narrative='本窗无合格新闻或社交，未提供分数。')
        self.assertIsNone(report.overall_score);self.assertIsNone(report.overall_band)
        text=render(report)
        self.assertIn('Unknown',text);self.assertIn('未提供分数',text)
        self.assertNotIn('5.0/10',text);self.assertNotIn('0.0/10',text)
        self.assertEqual(report.model_dump()['narrative'],'本窗无合格新闻或社交，未提供分数。')

    def test_missing_evidence_cannot_fill_neutral_five_or_zero(self):
        derived,_=A.make_sentiment_adapter(SmallReport,native_render,False)
        for score in (0,5,10):
            with self.subTest(score=score),self.assertRaises(ValidationError):
                derived(overall_band='Neutral',overall_score=score,confidence='low',narrative='没有证据却填分。')

    def test_half_null_missing_fields_empty_text_and_bad_enum_rejected(self):
        derived,_=A.make_sentiment_adapter(SmallReport,native_render,True)
        for patch in [{'overall_band':None},{'overall_score':None},{'narrative':' \n '},
                      {'overall_band':'Unknown'},{'overall_score':-0.01},{'overall_score':10.01}]:
            with self.subTest(patch=patch),self.assertRaises(ValidationError):derived.model_validate({**body(),**patch})
        for field in ('overall_band','overall_score'):
            data=body();data.pop(field)
            with self.assertRaises(ValidationError):derived.model_validate(data)

    def test_zero_ten_and_supported_neutral_still_use_native_render(self):
        derived,render=A.make_sentiment_adapter(SmallReport,native_render,True)
        for band,score in [('Bullish',0),('Bullish',10),('Neutral',5),('Mixed',4.5)]:
            data={**body(),'overall_band':band,'overall_score':score}
            self.assertEqual(render(derived.model_validate(data)),native_render(SmallReport.model_validate(data)))

    def test_prompt_replaces_only_three_anchored_lines(self):
        original='prefix\n'+A.NEWS_HEADING+'\n<data>原新闻，不可改</data>\nDuties unchanged.\n'+A.BAND_INSTRUCTION+'\n'+A.SCORE_INSTRUCTION+'\nconfidence and narrative unchanged\n后缀'
        changed=A.adapt_sentiment_system_message(original,has_evidence=False)
        self.assertNotIn('Yahoo Finance',changed);self.assertIn('No eligible score evidence',changed)
        old_lines=original.splitlines();new_lines=changed.splitlines()
        self.assertEqual(len(old_lines),len(new_lines))
        changed_indices=[i for i,(a,b) in enumerate(zip(old_lines,new_lines)) if a!=b]
        self.assertEqual(changed_indices,[1,4,5])
        for bad in [original.replace(A.NEWS_HEADING,'Changed upstream heading'),original+'\n'+A.BAND_INSTRUCTION]:
            with self.assertRaises(ValueError):A.adapt_sentiment_system_message(bad,has_evidence=False)

    def test_caller_evidence_decision_and_source_label_are_explicit(self):
        with self.assertRaises(TypeError):A.make_sentiment_adapter(SmallReport,native_render,'false')
        text=A.NEWS_HEADING+'\n'+A.BAND_INSTRUCTION+'\n'+A.SCORE_INSTRUCTION
        with self.assertRaises(ValueError):A.adapt_sentiment_system_message(text,has_evidence=True,news_source_label='source\nnew command')


if __name__=='__main__':unittest.main()
