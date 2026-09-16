"""Independent deterministic analysts. Inputs are immutable specialist DTOs."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from athena.market_data import Bar
from athena.models import SpecialistFinding


@dataclass(frozen=True)
class TechnicalInput:
    symbol: str
    bars: tuple[Bar, ...]
    references: tuple[str, ...] = ()
    benchmark_bars: tuple[Bar, ...] = ()

@dataclass(frozen=True)
class VolatilityInput:
    symbol: str
    bars: tuple[Bar, ...]
    references: tuple[str, ...] = ()

@dataclass(frozen=True)
class RegimeInput:
    symbol: str
    broad_bars: tuple[Bar, ...]
    references: tuple[str, ...] = ()

@dataclass(frozen=True)
class MacroInput:
    symbol: str
    series: tuple[tuple[str, tuple[Decimal, ...]], ...]
    references: tuple[str, ...] = ()

@dataclass(frozen=True)
class CompanyInput:
    symbol: str
    metrics: tuple[tuple[str, Decimal], ...]
    references: tuple[str, ...] = ()

@dataclass(frozen=True)
class NewsInput:
    symbol: str
    headlines: tuple[str, ...]
    references: tuple[str, ...] = ()

@dataclass(frozen=True)
class SentimentInput:
    symbol: str
    metrics: tuple[tuple[str, Decimal], ...]
    references: tuple[str, ...] = ()


def _missing(name: str, symbol: str, reason: str) -> SpecialistFinding:
    return SpecialistFinding(specialist=name, subject=symbol, errors=[reason], data_quality="UNAVAILABLE")


def technical(data: TechnicalInput) -> SpecialistFinding:
    bars = data.bars
    if len(bars) < 50: return _missing("Technical", data.symbol, "Need 50 bars for trend context")
    closes = [b.close for b in bars]
    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50
    latest = closes[-1]
    stance = "BULLISH" if latest > sma20 > sma50 else "BEARISH" if latest < sma20 < sma50 else "MIXED"
    support = min(bar.low for bar in bars[-20:])
    resistance = max(bar.high for bar in bars[-20:])
    momentum = latest / closes[-20] - 1 if closes[-20] else None
    volumes = [bar.volume for bar in bars[-20:] if bar.volume is not None]
    volume_ratio = bars[-1].volume / (sum(volumes) / len(volumes)) if volumes and bars[-1].volume is not None and sum(volumes) else None
    relative_strength = None
    if len(data.benchmark_bars) >= 20 and data.benchmark_bars[-20].close > 0 and closes[-20] > 0:
        benchmark_return = data.benchmark_bars[-1].close / data.benchmark_bars[-20].close - 1
        relative_strength = momentum - benchmark_return
    facts = [f"Latest close {latest}", f"SMA20 {sma20}", f"SMA50 {sma50}", f"20-session support {support}, resistance {resistance}"]
    metrics = {"close": latest, "sma20": sma20, "sma50": sma50, "support": support, "resistance": resistance, "momentum_20": momentum, "volume_ratio": volume_ratio, "relative_strength_20": relative_strength}
    return SpecialistFinding(specialist="Technical", subject=data.symbol, stance=stance, confidence=.65 if stance != "MIXED" else .3, facts=facts, inferences=[f"Price trend is {stance.lower()} under SMA20/SMA50 rule"], references=list(data.references), data_quality="AVAILABLE", metrics=metrics)


def volatility(data: VolatilityInput) -> SpecialistFinding:
    bars = data.bars
    if len(bars) < 21: return _missing("Volatility", data.symbol, "Need 21 bars for realized range")
    ranges = [bar.high - bar.low for bar in bars[-20:]]
    recent = sum(ranges[-5:]) / 5
    baseline = sum(ranges) / 20
    ratio = recent / baseline if baseline else Decimal(0)
    closes = [bar.close for bar in bars[-21:]]
    returns = [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1] > 0]
    realized = None
    if len(returns) >= 2:
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        realized = variance.sqrt() * Decimal(252).sqrt()
    stance = "ELEVATED" if ratio >= Decimal("1.5") else "NORMAL"
    return SpecialistFinding(specialist="Volatility", subject=data.symbol, stance=stance, confidence=.6, facts=[f"Recent mean range {recent}", f"20-session mean range {baseline}"], inferences=[f"Range regime {stance.lower()}"], risks=["Wide ranges can invalidate tight stops"] if stance == "ELEVATED" else [], references=list(data.references), data_quality="AVAILABLE", metrics={"range_ratio": ratio, "mean_range_20": baseline, "realized_volatility_annualized": realized}, errors=["Implied volatility unavailable"])


def regime(data: RegimeInput) -> SpecialistFinding:
    bars = data.broad_bars
    if len(bars) < 200: return _missing("MarketRegime", data.symbol, "Need 200 broad-market bars")
    closes = [bar.close for bar in bars]
    sma50, sma200 = sum(closes[-50:]) / 50, sum(closes[-200:]) / 200
    stance = "RISK_ON" if closes[-1] > sma50 > sma200 else "RISK_OFF" if closes[-1] < sma50 < sma200 else "MIXED"
    return SpecialistFinding(specialist="MarketRegime", subject=data.symbol, stance=stance, confidence=.6, facts=[f"Broad-market close {closes[-1]}", f"SMA50 {sma50}", f"SMA200 {sma200}"], inferences=[f"Broad market classified {stance}"], references=list(data.references), data_quality="AVAILABLE", metrics={"sma50": sma50, "sma200": sma200})


def macro(data: MacroInput) -> SpecialistFinding:
    changes = [(name, values[0] - values[1]) for name, values in data.series if len(values) >= 2]
    if not changes: return _missing("Macro", data.symbol, "No current and prior macro observations")
    headwinds = sum(change > 0 for _, change in changes)
    stance = "HEADWIND" if headwinds > len(changes) / 2 else "MIXED"
    return SpecialistFinding(specialist="Macro", subject=data.symbol, stance=stance, confidence=.4, facts=[f"{name}: change {change}" for name, change in changes], inferences=["Rising macro series are a simplified equity headwind proxy; interpretation varies by regime"], references=list(data.references), data_quality="PARTIAL", metrics={name: change for name, change in changes})


def company(data: CompanyInput) -> SpecialistFinding:
    metrics = dict(data.metrics)
    if not metrics: return _missing("Company", data.symbol, "No fundamentals available")
    growth = metrics.get("revenue_growth_yoy")
    debt = metrics.get("debt_to_equity")
    margin = metrics.get("operating_margin")
    strength = growth is not None and growth > 10 and (debt is None or debt < 2) and (margin is None or margin > 10)
    weakness = growth is not None and growth < 0 or debt is not None and debt > 3
    stance = "STRENGTH" if strength else "WEAKNESS" if weakness else "MIXED"
    risks = ["High debt-to-equity ratio"] if debt is not None and debt > 3 else []
    return SpecialistFinding(specialist="Company", subject=data.symbol, stance=stance, confidence=.45, facts=[f"{key}: {value}" for key, value in data.metrics], inferences=[f"Growth, margin and leverage screen: {stance}"], risks=risks, references=list(data.references), data_quality="PARTIAL", metrics=metrics)


def news(data: NewsInput) -> SpecialistFinding:
    if not data.headlines: return _missing("News", data.symbol, "No sourced recent headlines")
    positive = ("approved", "beats", "raises guidance", "buyback")
    negative = ("lawsuit", "misses", "cuts guidance", "bankruptcy")
    pos = sum(any(word in h.lower() for word in positive) for h in data.headlines)
    neg = sum(any(word in h.lower() for word in negative) for h in data.headlines)
    stance = "POSITIVE" if pos > neg else "NEGATIVE" if neg > pos else "MIXED"
    return SpecialistFinding(specialist="News", subject=data.symbol, stance=stance, confidence=.3, facts=list(data.headlines[:10]), inferences=[f"Lexicon flags: {pos} positive, {neg} negative; review context and negation"], references=list(data.references), data_quality="PARTIAL", metrics={"positive_flags": Decimal(pos), "negative_flags": Decimal(neg)})


def sentiment(data: SentimentInput) -> SpecialistFinding:
    metrics = dict(data.metrics)
    if not metrics: return _missing("Sentiment", data.symbol, "No positioning or sentiment observations")
    score = metrics.get("positive_score")
    stance = "POSITIVE" if score is not None and score > Decimal("0.3") else "NEGATIVE" if score is not None and score < Decimal("-0.3") else "MIXED"
    return SpecialistFinding(specialist="Sentiment", subject=data.symbol, stance=stance, confidence=.3, facts=[f"{key}: {value}" for key, value in data.metrics], inferences=[f"Social sentiment screen: {stance}"], references=list(data.references), data_quality="PARTIAL", metrics=metrics)


ANALYSTS = {"Technical": technical, "Volatility": volatility, "MarketRegime": regime, "Macro": macro, "Company": company, "News": news, "Sentiment": sentiment}
