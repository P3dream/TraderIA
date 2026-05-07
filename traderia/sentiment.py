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
    positive_terms = {
        "beat", "beats", "growth", "upgrade", "bullish", "profit", "record",
        "strong", "surge", "optimistic", "guidance", "outperform", "raised",
        "expansion", "recovery", "exceed", "exceeds", "innovation",
    }
    negative_terms = {
        "miss", "misses", "downgrade", "bearish", "loss", "weak", "fraud",
        "lawsuit", "cut", "risk", "decline", "layoffs", "recall", "probe",
        "investigation", "shortfall", "warning", "lowered", "default",
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


class ClaudeAPISentimentAnalyzer:
    """Sentiment analyzer using the Anthropic Claude API.

    Processes all headlines in a single request per call and returns a
    calibrated score in [-1, 1] with confidence metadata.
    """

    endpoint = "https://api.anthropic.com/v1/messages"
    api_version = "2023-06-01"

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        api_key: str | None = None,
        fallback: SentimentAnalyzer | None = None,
        timeout_seconds: int = 20,
    ) -> None:
        self.model = model
        self.api_key = os.getenv("ANTHROPIC_API_KEY") if api_key is None else api_key
        self.fallback = fallback or LexiconSentimentAnalyzer()
        self.timeout_seconds = timeout_seconds

    def score(self, headlines: list[str]) -> float:
        return self.analyze(headlines).score

    def analyze(self, headlines: list[str]) -> SentimentResult:
        if not headlines or not self.api_key:
            fallback_score = self.fallback.score(headlines)
            return SentimentResult(fallback_score, 0.35, "unknown", "fallback_lexicon")

        formatted = "\n".join(f"- {h}" for h in headlines)
        system_prompt = (
            "You are a financial market sentiment classifier for a paper-trading system. "
            "Given a batch of news headlines about a single stock, assess whether the overall "
            "news is positive or negative for that stock's near-term price (1-5 trading sessions). "
            "Account for financial jargon: 'earnings beat' is bullish, 'guidance cut' is bearish, "
            "'upgrade' is bullish, 'downgrade' is bearish. Consider negation: 'no growth' is bearish. "
            "Respond with ONLY a JSON object — no markdown, no explanation. "
            'Schema: {"score": <float -1.0 to 1.0>, "confidence": <float 0.0 to 1.0>, '
            '"impact_horizon": <"intraday"|"short_term"|"medium_term"|"unknown">, "reason": <string max 120 chars>}'
        )
        user_message = f"Headlines:\n{formatted}"

        payload = {
            "model": self.model,
            "max_tokens": 256,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }

        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": self.api_version,
                "content-type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
            text = body["content"][0]["text"].strip()
            parsed = json.loads(text)
            return SentimentResult(
                score=max(-1.0, min(1.0, float(parsed["score"]))),
                confidence=max(0.0, min(1.0, float(parsed["confidence"]))),
                impact_horizon=str(parsed.get("impact_horizon", "unknown")),
                reason=str(parsed.get("reason", "")),
            )
        except (KeyError, ValueError, TypeError, urllib.error.URLError, TimeoutError):
            fallback_score = self.fallback.score(headlines)
            return SentimentResult(fallback_score, 0.35, "unknown", "fallback_lexicon_after_claude_error")


class HybridSentimentAnalyzer:
    """Lexicon first, escalates to LLM when nuance matters."""

    escalation_terms = {
        "guidance", "earnings", "estimates", "forecast", "outlook", "regulatory",
        "sec", "antitrust", "lawsuit", "downgrade", "upgrade", "merger",
        "acquisition", "layoffs", "margin", "restatement", "investigation",
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
    """LLM sentiment analyzer using OpenAI's Responses API."""

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
    if normalized == "claude":
        return ClaudeAPISentimentAnalyzer(model=model if "claude" in model else "claude-haiku-4-5-20251001")
    if normalized == "hybrid":
        return HybridSentimentAnalyzer(llm=OpenAIResponsesSentimentAnalyzer(model=model))
    if normalized == "hybrid-claude":
        return HybridSentimentAnalyzer(llm=ClaudeAPISentimentAnalyzer())
    if normalized in {"openai", "codex", "llm"}:
        return OpenAIResponsesSentimentAnalyzer(model=model)
    return LexiconSentimentAnalyzer()
