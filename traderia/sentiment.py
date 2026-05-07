from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol


class SentimentAnalyzer(Protocol):
    def score(self, headlines: list[str]) -> float:
        ...


@dataclass(frozen=True)
class SentimentResult:
    score: float
    confidence: float
    impact_horizon: str
    reason: str


class LexiconSentimentAnalyzer:
    """Small local fallback until a real news/social sentiment provider is added."""

    positive_terms = {
        "beat",
        "beats",
        "growth",
        "upgrade",
        "bullish",
        "profit",
        "record",
        "strong",
        "surge",
        "optimistic",
        "guidance",
    }
    negative_terms = {
        "miss",
        "misses",
        "downgrade",
        "bearish",
        "loss",
        "weak",
        "fraud",
        "lawsuit",
        "cut",
        "risk",
        "decline",
    }

    def score(self, headlines: list[str]) -> float:
        if not headlines:
            return 0.0

        positive = 0
        negative = 0
        for headline in headlines:
            tokens = set(re.findall(r"[a-zA-Z]+", headline.lower()))
            positive += len(tokens & self.positive_terms)
            negative += len(tokens & self.negative_terms)

        total = positive + negative
        if total == 0:
            return 0.0
        return max(-1.0, min(1.0, (positive - negative) / total))


class HybridSentimentAnalyzer:
    """Use cheap lexical scoring first and ask the LLM only when nuance matters."""

    escalation_terms = {
        "guidance",
        "earnings",
        "estimates",
        "forecast",
        "outlook",
        "regulatory",
        "sec",
        "antitrust",
        "lawsuit",
        "downgrade",
        "upgrade",
        "merger",
        "acquisition",
        "layoffs",
        "margin",
    }

    def __init__(
        self,
        lexicon: SentimentAnalyzer | None = None,
        llm: SentimentAnalyzer | None = None,
        ambiguity_threshold: float = 0.35,
        relevance_threshold: int = 1,
    ) -> None:
        self.lexicon = lexicon or LexiconSentimentAnalyzer()
        self.llm = llm or OpenAIResponsesSentimentAnalyzer()
        self.ambiguity_threshold = ambiguity_threshold
        self.relevance_threshold = relevance_threshold

    def score(self, headlines: list[str]) -> float:
        lexical_score = self.lexicon.score(headlines)
        if not self._should_escalate(headlines, lexical_score):
            return lexical_score
        return self.llm.score(headlines)

    def _should_escalate(self, headlines: list[str], lexical_score: float) -> bool:
        if not headlines:
            return False
        if abs(lexical_score) <= self.ambiguity_threshold:
            return True

        text = " ".join(headlines).lower()
        terms_found = sum(1 for term in self.escalation_terms if term in text)
        return terms_found >= self.relevance_threshold


class OpenAIResponsesSentimentAnalyzer:
    """LLM sentiment analyzer using OpenAI's Responses API.

    It returns only a numeric score to keep the rest of the trading pipeline stable.
    The richer model output can be persisted later if we want explainability reports.
    """

    endpoint = "https://api.openai.com/v1/responses"

    def __init__(
        self,
        model: str = "gpt-5",
        api_key: str | None = None,
        fallback: SentimentAnalyzer | None = None,
        timeout_seconds: int = 20,
    ) -> None:
        self.model = model
        self.api_key = os.getenv("OPENAI_API_KEY") if api_key is None else api_key
        self.fallback = fallback or LexiconSentimentAnalyzer()
        self.timeout_seconds = timeout_seconds

    def score(self, headlines: list[str]) -> float:
        return self.analyze(headlines).score

    def analyze(self, headlines: list[str]) -> SentimentResult:
        if not headlines or not self.api_key:
            fallback_score = self.fallback.score(headlines)
            return SentimentResult(fallback_score, 0.35, "unknown", "fallback lexicon")

        payload = {
            "model": self.model,
            "instructions": (
                "You are a financial-market sentiment classifier for a paper-trading agent. "
                "Assess whether the provided headlines are likely positive or negative for the named stock "
                "over the next few trading sessions. Return calibrated JSON only. "
                "Scores must be between -1.0 and 1.0, where -1 is strongly bearish, 0 is neutral, and 1 is strongly bullish. "
                "Do not provide financial advice or trade recommendations."
            ),
            "input": "\n".join(f"- {headline}" for headline in headlines),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "market_sentiment",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "score": {"type": "number", "minimum": -1, "maximum": 1},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "impact_horizon": {
                                "type": "string",
                                "enum": ["intraday", "short_term", "medium_term", "unknown"],
                            },
                            "reason": {"type": "string"},
                        },
                        "required": ["score", "confidence", "impact_horizon", "reason"],
                    },
                }
            },
        }

        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
            parsed = self._parse_response(body)
            return SentimentResult(
                score=max(-1.0, min(1.0, float(parsed["score"]))),
                confidence=max(0.0, min(1.0, float(parsed["confidence"]))),
                impact_horizon=str(parsed["impact_horizon"]),
                reason=str(parsed["reason"]),
            )
        except (KeyError, ValueError, TypeError, urllib.error.URLError, TimeoutError):
            fallback_score = self.fallback.score(headlines)
            return SentimentResult(fallback_score, 0.35, "unknown", "fallback lexicon after OpenAI error")

    def _parse_response(self, body: dict) -> dict:
        if "output_text" in body:
            return json.loads(body["output_text"])

        for item in body.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"} and "text" in content:
                    return json.loads(content["text"])
        raise ValueError("Response did not include parseable JSON text")


def build_sentiment_analyzer(provider: str, model: str = "gpt-5") -> SentimentAnalyzer:
    normalized = provider.lower()
    if normalized == "hybrid":
        return HybridSentimentAnalyzer(llm=OpenAIResponsesSentimentAnalyzer(model=model))
    if normalized in {"openai", "codex", "llm"}:
        return OpenAIResponsesSentimentAnalyzer(model=model)
    return LexiconSentimentAnalyzer()
