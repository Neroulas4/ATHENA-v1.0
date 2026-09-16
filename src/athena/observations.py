"""Evidence-bound, deterministic Post-Mortem pattern observations."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .evaluation import horizon_end
from .market_data import MarketDataProvider
from .models import Direction, LessonContext, Outcome, Prediction, SpecialistFinding


@dataclass(frozen=True)
class PatternObservation:
    pattern: str
    category: str
    statement: str
    context: LessonContext
    supports: bool = True


def generate_observations(prediction: Prediction, outcome: Outcome, findings: tuple[SpecialistFinding, ...], provider: MarketDataProvider) -> tuple[PatternObservation, ...]:
    if outcome.outcome_status not in {"COMPLETED", "EXPIRED"} or outcome.data_quality != "AVAILABLE":
        return ()
    own = {f.specialist: f for f in findings if f.subject == prediction.subject and f.data_quality != "UNAVAILABLE"}
    setup_type = "BREAKOUT" if prediction.trigger_operator in {"ABOVE", "BELOW"} else next(iter(prediction.methodology_tags), None)
    regime = own.get("MarketRegime")
    volatility = own.get("Volatility")
    technical = own.get("Technical")
    news = own.get("News")
    sentiment = own.get("Sentiment")
    direction = prediction.direction
    base = dict(direction=direction, setup_type=setup_type)
    result: list[PatternObservation] = []

    def add(pattern: str, category: str, statement: str, **scope) -> None:
        result.append(PatternObservation(pattern, category, statement, LessonContext(**(base | scope))))

    if outcome.trigger_activated is False and outcome.horizon_expired is True:
        add("TRIGGER_EXPIRED", "timing", "Structured entry failed to activate before expiry", horizon_tag=prediction.horizon)
    if outcome.trigger_activated is True:
        end = horizon_end(prediction)
        if outcome.activated_at and end and prediction.timestamp < outcome.activated_at <= end and (outcome.activated_at - prediction.timestamp) >= (end - prediction.timestamp) * .75:
            add("LATE_ACTIVATION", "timing", "Entry activated in the final quarter of its horizon", horizon_tag=prediction.horizon)
        if outcome.timing_correct is False and outcome.thesis_correct is True:
            add("TIMING_MISS_DIRECTION_RIGHT", "timing", "Directional thesis was correct but target timing failed", horizon_tag=prediction.horizon)
        if outcome.stop_hit is True and outcome.tp1_hit is False:
            add("STOP_BEFORE_TARGET", "risk", "Stop was reached before the first target")
        if prediction.risk_reward is not None and prediction.risk_reward < Decimal("1.5") and outcome.thesis_correct is False:
            add("POOR_RISK_REWARD", "risk", "Weak planned risk/reward coincided with a failed thesis")
        if outcome.maximum_adverse_excursion is not None and outcome.maximum_adverse_excursion <= Decimal("-0.05"):
            add("LARGE_ADVERSE_EXCURSION", "risk", "Adverse excursion exceeded five percent")
    if volatility is not None:
        mean_range = volatility.metrics.get("mean_range_20")
        try: mean_range = Decimal(str(mean_range)) if mean_range is not None else None
        except ValueError: mean_range = None
        range_ratio = volatility.metrics.get("range_ratio")
        try: range_ratio = Decimal(str(range_ratio)) if range_ratio is not None else None
        except ValueError: range_ratio = None
        if range_ratio is not None and range_ratio < Decimal("0.75") and outcome.trigger_activated is False and outcome.horizon_expired is True:
            add("COMPRESSION_TRIGGER_EXPIRED", "volatility", "Breakout entry expired during observed range compression", volatility_regime=volatility.stance, risk_pattern="COMPRESSION")
        if range_ratio is not None and range_ratio >= Decimal("1.5") and outcome.trigger_activated is True and outcome.timing_correct is True:
            add("EXPANSION_TRIGGER_FOLLOW_THROUGH", "volatility", "Breakout entry followed through during observed range expansion", volatility_regime=volatility.stance, risk_pattern="EXPANSION")
        if mean_range and mean_range > 0 and prediction.entry_reference_level and prediction.stop_level and outcome.stop_hit is True:
            distance = abs(prediction.entry_reference_level - prediction.stop_level)
            if volatility.stance == "ELEVATED" and distance < mean_range:
                add("TIGHT_STOP_HIGH_VOL", "risk", "Stop distance was below observed mean range in elevated volatility", volatility_regime="ELEVATED", risk_pattern="TIGHT_STOP")
            if prediction.tp1 and abs(prediction.tp1 - prediction.entry_reference_level) > 4 * mean_range and outcome.tp1_hit is False:
                add("TARGET_TOO_FAR_FOR_RANGE", "risk", "First target exceeded four observed mean ranges without being reached", volatility_regime=volatility.stance)
        if volatility.stance == "ELEVATED" and outcome.thesis_correct is False:
            add("HIGH_VOL_FAILURE", "volatility", "Thesis failed in elevated range regime", volatility_regime="ELEVATED")
    if regime is not None and outcome.thesis_correct is False:
        if direction == Direction.LONG and regime.stance == "RISK_OFF" or direction == Direction.SHORT and regime.stance == "RISK_ON":
            add("REGIME_CONFLICT_FAILURE", "regime", "Directional setup failed against the observed broad market regime", market_regime=regime.stance)
    if outcome.trigger_activated is True and outcome.thesis_correct is not None and prediction.instrument and prediction.instrument != "SPY":
        end = horizon_end(prediction)
        if end:
            instrument = provider.historical_bars(prediction.instrument, prediction.timestamp, end) or []
            benchmark = provider.historical_bars("SPY", prediction.timestamp, end) or []
            if len(instrument) >= 2 and len(benchmark) >= 2 and instrument[0].close > 0 and benchmark[0].close > 0:
                relative = instrument[-1].close / instrument[0].close - benchmark[-1].close / benchmark[0].close
                if direction == Direction.LONG and relative < 0:
                    add("LONG_RELATIVE_UNDERPERFORMANCE", "relative_strength", "Selected LONG underperformed the available SPY benchmark", relative_strength="WEAK")
                if direction == Direction.SHORT and relative > 0:
                    add("SHORT_RELATIVE_STRENGTH", "relative_strength", "Selected SHORT showed relative strength versus available SPY benchmark", relative_strength="STRONG")
    if news is not None and news.references and outcome.thesis_correct is False and news.stance == ("POSITIVE" if direction == Direction.LONG else "NEGATIVE"):
        add("CATALYST_NO_FOLLOW_THROUGH", "catalyst", "Sourced directional news did not coincide with favorable price follow-through", catalyst_type="NEWS")
    if news is not None and news.references and outcome.thesis_correct is True and news.stance == ("POSITIVE" if direction == Direction.LONG else "NEGATIVE"):
        add("CATALYST_FOLLOW_THROUGH", "catalyst", "Sourced directional news coincided with favorable price follow-through", catalyst_type="NEWS")
    opposition = {"BEARISH", "NEGATIVE", "WEAKNESS", "HEADWIND"} if direction == Direction.LONG else {"BULLISH", "POSITIVE", "STRENGTH", "TAILWIND"}
    dissent = tuple(sorted(f.specialist for f in own.values() if f.stance in opposition))
    if dissent and outcome.thesis_correct is False:
        add("DISSENT_FAILURE", "disagreement", "Original analyst dissent coincided with a failed thesis", analyst_tags=dissent)
    company = own.get("Company")
    if technical and company and ((technical.stance in opposition) != (company.stance in opposition)) and outcome.thesis_correct is False:
        add("TECHNICAL_COMPANY_CONFLICT", "disagreement", "Technical and Company views conflicted before failure", analyst_tags=("Company", "Technical"))
    if news and news.references and (technical is None or technical.stance == "MIXED") and outcome.thesis_correct is False:
        add("NEWS_WITHOUT_TECHNICAL_CONFIRMATION", "disagreement", "Sourced news lacked directional Technical confirmation before failure", analyst_tags=("News",))
    company_support = company and company.references and company.stance == ("STRENGTH" if direction == Direction.LONG else "WEAKNESS")
    news_support = news and news.references and news.stance == ("POSITIVE" if direction == Direction.LONG else "NEGATIVE")
    if technical and technical.references and technical.stance == ("BULLISH" if direction == Direction.LONG else "BEARISH") and not company_support and not news_support and outcome.thesis_correct is False:
        add("TECHNICAL_WITHOUT_CATALYST", "disagreement", "Directional Technical signal lacked sourced Company or News support before failure", analyst_tags=("Technical",))
    if sentiment is not None:
        score = sentiment.metrics.get("positive_score")
        try: score = Decimal(str(score)) if score is not None else None
        except ValueError: score = None
        if score is not None and direction == Direction.LONG and score >= Decimal("0.8") and outcome.trigger_activated is False:
            add("CROWDED_BULLISH_NO_CONFIRMATION", "sentiment", "Crowded bullish sentiment lacked entry confirmation")
        if score is not None and direction == Direction.SHORT and score <= Decimal("-0.8") and technical and technical.stance == "BULLISH" and outcome.thesis_correct is False:
            add("BEARISH_CROWDING_TECHNICAL_RECOVERY", "sentiment", "Bearish sentiment conflicted with improving Technical evidence before SHORT failure", analyst_tags=("Sentiment", "Technical"))
    if prediction.quality_score is not None and outcome.thesis_correct is not None:
        bucket = "LOW" if prediction.quality_score < .8 else "HIGH"
        if bucket == "LOW" and outcome.thesis_correct is False or bucket == "HIGH" and outcome.thesis_correct is True:
            add(f"{bucket}_QUALITY_{'FAILURE' if bucket == 'LOW' else 'FOLLOW_THROUGH'}", "quality", f"{bucket.title()} quality setup {'failed' if bucket == 'LOW' else 'followed through'}", risk_pattern=f"QUALITY_{bucket}")
    return tuple(result)
