"""Vendor boundary. Analysts receive snapshots and never import providers."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

import httpx


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Bar timestamp must be timezone-aware")
        for key in ("open", "high", "low", "close", "volume"):
            value = getattr(self, key)
            if value is not None:
                decimal = Decimal(str(value))
                if not decimal.is_finite(): raise ValueError("Non-finite market value")
                object.__setattr__(self, key, decimal)
        if self.low > self.high or not self.low <= self.open <= self.high or not self.low <= self.close <= self.high:
            raise ValueError("Invalid OHLC bar")


class MarketDataProvider(Protocol):
    name: str
    def current_price(self, instrument: str) -> Decimal | None: ...
    def historical_bars(self, instrument: str, start: datetime, end: datetime) -> list[Bar] | None: ...
    def metadata(self, instrument: str) -> dict[str, str] | None: ...


class UnavailableMarketDataProvider:
    name = "unavailable"
    def current_price(self, instrument: str) -> None: return None
    def historical_bars(self, instrument: str, start: datetime, end: datetime) -> None: return None
    def metadata(self, instrument: str) -> dict[str, str]: return {"status": "UNAVAILABLE", "provider": self.name}


class FakeMarketDataProvider:
    name = "fake"
    def __init__(self, bars: dict[str, list[Bar]] | None = None) -> None: self.bars = bars or {}
    def current_price(self, instrument: str) -> Decimal | None:
        values = self.bars.get(instrument, [])
        return values[-1].close if values else None
    def historical_bars(self, instrument: str, start: datetime, end: datetime) -> list[Bar] | None:
        return [bar for bar in self.bars.get(instrument, []) if start <= bar.timestamp <= end]
    def metadata(self, instrument: str) -> dict[str, str]:
        return {"status": "AVAILABLE" if instrument in self.bars else "UNAVAILABLE", "provider": self.name, "feed": "deterministic-test"}


class AlpacaMarketDataProvider:
    """Alpaca stock bars using the IEX single-exchange feed."""
    name = "alpaca"
    def __init__(self, key: str | None = None, secret: str | None = None, client: httpx.Client | None = None) -> None:
        self.key = key or os.getenv("ALPACA_API_KEY_ID", "")
        self.secret = secret or os.getenv("ALPACA_API_SECRET_KEY", "")
        self.client = client or httpx.Client(timeout=10)
    def historical_bars(self, instrument: str, start: datetime, end: datetime) -> list[Bar] | None:
        if not self.key or not self.secret: return None
        response = self.client.get(f"https://data.alpaca.markets/v2/stocks/{instrument}/bars", headers={"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret}, params={"timeframe": "1Day", "start": start.isoformat(), "end": end.isoformat(), "feed": "iex", "limit": 10000})
        response.raise_for_status()
        return [Bar(datetime.fromisoformat(b["t"].replace("Z", "+00:00")), b["o"], b["h"], b["l"], b["c"], b.get("v")) for b in response.json().get("bars", [])]
    def current_price(self, instrument: str) -> Decimal | None:
        if not self.key or not self.secret: return None
        response = self.client.get(f"https://data.alpaca.markets/v2/stocks/{instrument}/trades/latest", headers={"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret}, params={"feed": "iex"})
        response.raise_for_status()
        value = response.json().get("trade", {}).get("p")
        return Decimal(str(value)) if value is not None else None
    def metadata(self, instrument: str) -> dict[str, str]:
        return {"status": "CONFIGURED" if self.key and self.secret else "UNAVAILABLE", "provider": self.name, "feed": "iex-single-exchange"}


class YFinanceMarketDataProvider:
    """Optional unofficial historical fallback; never execution data."""
    name = "yfinance"
    def historical_bars(self, instrument: str, start: datetime, end: datetime) -> list[Bar] | None:
        try: import yfinance as yf
        except ImportError: return None
        frame = yf.Ticker(instrument).history(start=start.date().isoformat(), end=end.date().isoformat(), auto_adjust=False)
        bars = []
        for index, row in frame.iterrows():
            stamp = index.to_pydatetime()
            if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
            bars.append(Bar(stamp, row["Open"], row["High"], row["Low"], row["Close"], row.get("Volume")))
        return bars
    def current_price(self, instrument: str) -> None: return None
    def metadata(self, instrument: str) -> dict[str, str]: return {"status": "HISTORICAL_ONLY", "provider": self.name, "feed": "unofficial-yahoo"}


class MarketDataService:
    def __init__(self, providers: list[MarketDataProvider] | None = None) -> None:
        self.providers = providers or [UnavailableMarketDataProvider()]
        self.last_status: dict[str, str] = {}
    def historical_bars(self, instrument: str, start: datetime, end: datetime) -> list[Bar] | None:
        for provider in self.providers:
            try:
                bars = provider.historical_bars(instrument, start, end)
                if bars:
                    self.last_status[instrument] = provider.name
                    return sorted(bars, key=lambda bar: bar.timestamp)
            except Exception as exc:
                self.last_status[instrument] = f"{provider.name}: {type(exc).__name__}"
        self.last_status[instrument] = "UNAVAILABLE"
        return None
    def current_price(self, instrument: str) -> Decimal | None:
        for provider in self.providers:
            try:
                price = provider.current_price(instrument)
                if price is not None:
                    self.last_status[instrument] = provider.name
                    return price
            except Exception as exc:
                self.last_status[instrument] = f"{provider.name}: {type(exc).__name__}"
        return None


def configured_market_data_service() -> MarketDataService:
    providers: list[MarketDataProvider] = []
    if os.getenv("ALPACA_API_KEY_ID") and os.getenv("ALPACA_API_SECRET_KEY"):
        providers.append(AlpacaMarketDataProvider())
    if os.getenv("ATHENA_ENABLE_YFINANCE") == "1": providers.append(YFinanceMarketDataProvider())
    return MarketDataService(providers or [UnavailableMarketDataProvider()])
