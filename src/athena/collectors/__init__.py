"""Source collectors. All price data enters through MarketDataService."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from athena.agents.deterministic import CompanyInput, MacroInput, NewsInput, RegimeInput, SentimentInput, TechnicalInput, VolatilityInput
from athena.market_data import MarketDataService
from athena.models import Evidence


class CollectorService:
    def __init__(self, market: MarketDataService, client: httpx.Client | None = None) -> None:
        self.market = market
        self.client = client or httpx.Client(timeout=10)
        self.errors: list[str] = []

    def collect(self, symbol: str, selected: set[str], run_id: str) -> tuple[dict[str, object], list[Evidence]]:
        now = datetime.now(timezone.utc)
        inputs: dict[str, object] = {}
        evidence: list[Evidence] = []
        market_names = {"Technical", "Volatility", "MarketRegime"}
        if selected & market_names:
            bars = self.market.historical_bars(symbol, now - timedelta(days=400), now) or []
            ref = f"market:{self.market.last_status.get(symbol, 'UNAVAILABLE')}:{symbol}"
            if bars:
                evidence.append(Evidence(run_id=run_id, source=self.market.last_status[symbol], reference=ref, content=f"{len(bars)} OHLCV bars; last close {bars[-1].close}", subject=symbol, quality="PARTIAL" if "alpaca" in ref else "AVAILABLE", data_status="HISTORICAL", observed_at=bars[-1].timestamp))
            broad = self.market.historical_bars("SPY", now - timedelta(days=400), now) or [] if ("MarketRegime" in selected or "Technical" in selected) and symbol != "SPY" else (bars if symbol == "SPY" else [])
            if "Technical" in selected: inputs["Technical"] = TechnicalInput(symbol, tuple(bars), (ref,), tuple(broad))
            if "Volatility" in selected: inputs["Volatility"] = VolatilityInput(symbol, tuple(bars), (ref,))
            if "MarketRegime" in selected:
                inputs["MarketRegime"] = RegimeInput(symbol, tuple(broad), (f"market:{self.market.last_status.get('SPY', 'UNAVAILABLE')}:SPY",))
        if "Macro" in selected:
            series = self._fred()
            inputs["Macro"] = MacroInput(symbol, tuple(series.items()), tuple(f"https://fred.stlouisfed.org/series/{key}" for key in series))
            for key, values in series.items():
                evidence.append(Evidence(run_id=run_id, source="FRED", reference=f"https://fred.stlouisfed.org/series/{key}", content=f"Latest {values[0]} and prior {values[1] if len(values)>1 else 'unavailable'}", subject=symbol, quality="AVAILABLE", data_status="HISTORICAL"))
        if "Company" in selected:
            values = self._finnhub_metrics(symbol)
            inputs["Company"] = CompanyInput(symbol, tuple(values.items()), (f"https://finnhub.io/api/v1/stock/metric?symbol={symbol}",) if values else ())
            for key, value in values.items():
                evidence.append(Evidence(run_id=run_id, source="Finnhub", reference=f"finnhub:metric:{symbol}:{key}", content=str(value), subject=symbol, quality="PARTIAL", data_status="CURRENT"))
        if "News" in selected:
            articles = self._finnhub_news(symbol)
            inputs["News"] = NewsInput(symbol, tuple(item[0] for item in articles), tuple(item[1] for item in articles))
            for headline, url in articles:
                evidence.append(Evidence(run_id=run_id, source="Finnhub", reference=url, content=headline, subject=symbol, quality="PARTIAL", data_status="CURRENT"))
        if "Sentiment" in selected:
            values = self._finnhub_social(symbol)
            inputs["Sentiment"] = SentimentInput(symbol, tuple(values.items()), (f"finnhub:social:{symbol}",) if values else ())
            for key, value in values.items():
                evidence.append(Evidence(run_id=run_id, source="Finnhub", reference=f"finnhub:social:{symbol}:{key}", content=str(value), subject=symbol, quality="PARTIAL", data_status="CURRENT"))
        return inputs, evidence

    def _get(self, url: str, params: dict[str, str]) -> object | None:
        try:
            response = self.client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            self.errors.append(f"{url}: {type(exc).__name__}")
            return None

    def _fred(self) -> dict[str, tuple[Decimal, ...]]:
        key = os.getenv("FRED_API_KEY")
        if not key: return {}
        result = {}
        for series in ("FEDFUNDS", "UNRATE", "DGS10", "CPIAUCSL"):
            data = self._get("https://api.stlouisfed.org/fred/series/observations", {"series_id": series, "api_key": key, "file_type": "json", "sort_order": "desc", "limit": "14"})
            if isinstance(data, dict):
                values = tuple(Decimal(str(item["value"])) for item in data.get("observations", []) if item.get("value") not in (None, "."))
                if values: result[series] = values
        return result

    def _finnhub_metrics(self, symbol: str) -> dict[str, Decimal]:
        key = os.getenv("FINNHUB_API_KEY")
        if not key: return {}
        data = self._get("https://finnhub.io/api/v1/stock/metric", {"symbol": symbol, "metric": "all", "token": key})
        if not isinstance(data, dict): return {}
        raw = data.get("metric", {})
        names = {"revenueGrowthTTMYoy": "revenue_growth_yoy", "epsGrowthTTMYoy": "eps_growth_yoy", "totalDebt/totalEquityQuarterly": "debt_to_equity", "operatingMarginTTM": "operating_margin", "roeTTM": "roe"}
        return {alias: Decimal(str(raw[name])) for name, alias in names.items() if raw.get(name) is not None}

    def _finnhub_news(self, symbol: str) -> list[tuple[str, str]]:
        key = os.getenv("FINNHUB_API_KEY")
        if not key: return []
        today = datetime.now(timezone.utc).date()
        data = self._get("https://finnhub.io/api/v1/company-news", {"symbol": symbol, "from": (today - timedelta(days=3)).isoformat(), "to": today.isoformat(), "token": key})
        if not isinstance(data, list): return []
        return [(str(item.get("headline", "")), str(item.get("url", ""))) for item in data[:25] if item.get("headline") and item.get("url")]

    def _finnhub_social(self, symbol: str) -> dict[str, Decimal]:
        key = os.getenv("FINNHUB_API_KEY")
        if not key: return {}
        data = self._get("https://finnhub.io/api/v1/stock/social-sentiment", {"symbol": symbol, "token": key})
        if not isinstance(data, dict): return {}
        posts = data.get("reddit", []) + data.get("twitter", [])
        positive = sum(Decimal(str(item.get("positiveMention", 0))) for item in posts)
        negative = sum(Decimal(str(item.get("negativeMention", 0))) for item in posts)
        total = positive + negative
        return {"positive_score": (positive - negative) / total} if total else {}
