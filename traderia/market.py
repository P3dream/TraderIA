from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from traderia.models import MarketBar


class MarketDataProvider(Protocol):
    def history(self, symbols: list[str], days: int) -> dict[str, list[MarketBar]]:
        ...


class SyntheticMarketDataProvider:
    """Deterministic market stream for paper-trading development and tests."""

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed

    def history(self, symbols: list[str], days: int) -> dict[str, list[MarketBar]]:
        result: dict[str, list[MarketBar]] = {}
        end = datetime.now().replace(hour=16, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=days)

        for index, symbol in enumerate(symbols):
            rng = random.Random(f"{self.seed}:{symbol}")
            price = 80.0 + index * 30.0 + rng.random() * 20.0
            bars: list[MarketBar] = []

            for day in range(days):
                timestamp = start + timedelta(days=day)
                drift = 0.0008 + index * 0.0002
                cycle = math.sin(day / 7.0 + index) * 0.012
                shock = rng.gauss(0, 0.018)
                previous = price
                price = max(1.0, price * (1 + drift + cycle + shock))
                high = max(previous, price) * (1 + rng.random() * 0.01)
                low = min(previous, price) * (1 - rng.random() * 0.01)
                volume = int(800_000 + rng.random() * 1_500_000 + abs(shock) * 20_000_000)
                bars.append(
                    MarketBar(
                        symbol=symbol,
                        timestamp=timestamp,
                        open=round(previous, 2),
                        high=round(high, 2),
                        low=round(low, 2),
                        close=round(price, 2),
                        volume=volume,
                    )
                )

            result[symbol] = bars

        return result


def build_market_provider(
    provider: str,
    yahoo_cache_dir: str = "data/yahoo_cache",
) -> MarketDataProvider:
    normalized = provider.lower()
    if normalized in {"yahoo", "yahoo-chart", "yfinance"}:
        return YahooChartMarketDataProvider(cache_dir=yahoo_cache_dir)
    return SyntheticMarketDataProvider()


class YahooChartMarketDataProvider:
    endpoint = "https://query1.finance.yahoo.com/v8/finance/chart"

    def __init__(self, timeout_seconds: int = 30, cache_dir: str = "data/yahoo_cache") -> None:
        self.timeout_seconds = timeout_seconds
        self.cache_dir = Path(cache_dir)

    def history(self, symbols: list[str], days: int) -> dict[str, list[MarketBar]]:
        histories: dict[str, list[MarketBar]] = {}
        for symbol in symbols:
            histories[symbol] = self._daily_history(symbol, days)
        return histories

    def _daily_history(self, symbol: str, days: int) -> list[MarketBar]:
        cached = self._read_cache(symbol)
        if cached is not None and len(cached) >= days:
            return cached[-days:]

        params = urlencode({"range": self._range_for_days(days), "interval": "1d"})
        request = Request(
            f"{self.endpoint}/{symbol.upper()}?{params}",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))

        bars = self._parse_payload(symbol, payload)
        if len(bars) < days:
            raise RuntimeError(f"Yahoo returned only {len(bars)} bars for {symbol}; requested {days}")
        self._write_cache(symbol, bars)
        return bars[-days:]

    def _parse_payload(self, symbol: str, payload: dict) -> list[MarketBar]:
        chart = payload.get("chart", {})
        error = chart.get("error")
        if error:
            raise RuntimeError(f"Yahoo rejected {symbol}: {error}")
        results = chart.get("result") or []
        if not results:
            raise RuntimeError(f"Yahoo did not return daily prices for {symbol}")

        result = results[0]
        timestamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        bars: list[MarketBar] = []
        for index, timestamp in enumerate(timestamps):
            values = (opens[index], highs[index], lows[index], closes[index], volumes[index])
            if any(value is None for value in values):
                continue
            bars.append(
                MarketBar(
                    symbol=symbol.upper(),
                    timestamp=datetime.fromtimestamp(timestamp).replace(hour=16, minute=0, second=0, microsecond=0),
                    open=float(opens[index]),
                    high=float(highs[index]),
                    low=float(lows[index]),
                    close=float(closes[index]),
                    volume=int(volumes[index]),
                )
            )
        return bars

    def _range_for_days(self, days: int) -> str:
        if days <= 126:
            return "6mo"
        if days <= 252:
            return "1y"
        if days <= 504:
            return "2y"
        return "5y"

    def _cache_path(self, symbol: str) -> Path:
        return self.cache_dir / f"{symbol.upper()}_daily.json"

    def _read_cache(self, symbol: str) -> list[MarketBar] | None:
        path = self._cache_path(symbol)
        if not path.exists():
            return None
        if datetime.now() - datetime.fromtimestamp(path.stat().st_mtime) > timedelta(hours=18):
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [
            MarketBar(
                symbol=item["symbol"],
                timestamp=datetime.fromisoformat(item["timestamp"]),
                open=float(item["open"]),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
                volume=int(item["volume"]),
            )
            for item in payload["bars"]
        ]

    def _write_cache(self, symbol: str, bars: list[MarketBar]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "symbol": symbol.upper(),
            "bars": [
                {
                    "symbol": bar.symbol,
                    "timestamp": bar.timestamp.isoformat(),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
                for bar in bars
            ],
        }
        self._cache_path(symbol).write_text(json.dumps(payload), encoding="utf-8")
