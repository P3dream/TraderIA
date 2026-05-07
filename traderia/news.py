from __future__ import annotations

import json
from typing import Protocol
from urllib.parse import quote
from urllib.request import Request, urlopen


class NewsProvider(Protocol):
    def headlines(self, symbol: str, day_index: int) -> list[str]:
        ...


class NeutralNewsProvider:
    def headlines(self, symbol: str, day_index: int) -> list[str]:
        return []


class SyntheticNewsProvider:
    positive = [
        "{symbol} beats estimates with strong guidance",
        "Analysts upgrade {symbol} after record growth",
        "{symbol} shows bullish momentum in core business",
    ]
    negative = [
        "{symbol} faces weak demand and downgrade risk",
        "Bearish outlook pressures {symbol} after profit miss",
        "{symbol} lawsuit raises investor concern",
    ]
    neutral = [
        "{symbol} trades mixed as market awaits data",
        "{symbol} volume rises during sector rotation",
        "Investors watch {symbol} before next earnings update",
    ]

    def headlines(self, symbol: str, day_index: int) -> list[str]:
        bucket = day_index % 9
        if bucket in {0, 1, 2}:
            templates = self.positive
        elif bucket in {6, 7}:
            templates = self.negative
        else:
            templates = self.neutral
        return [templates[day_index % len(templates)].format(symbol=symbol)]


class YahooNewsProvider:
    endpoint = "https://query1.finance.yahoo.com/v1/finance/search"

    def __init__(self, timeout_seconds: int = 20, max_headlines: int = 6) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_headlines = max_headlines
        self.cache: dict[str, list[str]] = {}

    def headlines(self, symbol: str, day_index: int) -> list[str]:
        if symbol not in self.cache:
            self.cache[symbol] = self._fetch(symbol)
        return self.cache[symbol]

    def _fetch(self, symbol: str) -> list[str]:
        request = Request(
            f"{self.endpoint}?q={quote(symbol)}&quotesCount=1&newsCount={self.max_headlines}",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))

        headlines: list[str] = []
        for item in payload.get("news", []):
            title = item.get("title")
            if isinstance(title, str) and title.strip():
                headlines.append(title.strip())
        return headlines


def build_news_provider(provider: str) -> NewsProvider:
    normalized = provider.lower()
    if normalized == "synthetic":
        return SyntheticNewsProvider()
    if normalized == "yahoo":
        return YahooNewsProvider()
    return NeutralNewsProvider()

