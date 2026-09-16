"""Deterministic setup evaluation. OHLC ambiguity stays pending."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from .market_data import MarketDataProvider
from .models import Direction, Outcome, Prediction, SetupState, ExecutionState, now_utc

_HORIZON = re.compile(r"^(\d+)\s*([dhw])$", re.I)


def horizon_end(prediction: Prediction) -> datetime | None:
    match = _HORIZON.match(prediction.horizon.strip())
    if not match: return None
    count, unit = int(match.group(1)), match.group(2).lower()
    return prediction.timestamp + timedelta(hours=count if unit == "h" else 24 * count * (7 if unit == "w" else 1))


@dataclass(frozen=True)
class TriggerEvaluation:
    available: bool
    activated: bool | None
    stop_hit: bool | None
    tp1_hit: bool | None
    tp2_hit: bool | None
    realized_move: Decimal | None
    mae: Decimal | None
    mfe: Decimal | None
    horizon_expired: bool | None
    notes: str
    setup_state: SetupState = SetupState.NOT_ACTIVE_YET
    timing_correct: bool | None = None
    activated_at: datetime | None = None


def _operator(prediction: Prediction) -> str | None:
    if prediction.trigger_operator: return prediction.trigger_operator
    if prediction.entry_condition:
        condition = prediction.entry_condition.lower()
        if "above" in condition: return "ABOVE"
        if "below" in condition: return "BELOW"
    return None


def evaluate_trigger(prediction: Prediction, provider: MarketDataProvider, now: datetime | None = None) -> TriggerEvaluation:
    end = horizon_end(prediction)
    operator = _operator(prediction)
    if prediction.direction not in (Direction.LONG, Direction.SHORT) or not prediction.instrument or prediction.entry_reference_level is None or operator is None or end is None:
        return TriggerEvaluation(False, None, None, None, None, None, None, None, None, "Structured direction, instrument, entry, operator and finite horizon required.")
    current = now or now_utc()
    if current < prediction.timestamp: current = prediction.timestamp
    query_end = min(current, end)
    bars = provider.historical_bars(prediction.instrument, prediction.timestamp, query_end)
    if not bars:
        return TriggerEvaluation(False, None, None, None, None, None, None, None, current >= end, "Market data unavailable or no bars in horizon.")
    bars = sorted((bar for bar in bars if prediction.timestamp <= bar.timestamp <= query_end), key=lambda bar: bar.timestamp)
    if not bars:
        return TriggerEvaluation(False, None, None, None, None, None, None, None, current >= end, "No bars in evaluation window.")
    level = prediction.entry_reference_level
    reached = lambda bar: bar.high >= level if operator == "ABOVE" else bar.low <= level
    first = next((i for i, bar in enumerate(bars) if reached(bar)), None)
    expired = current >= end and bars[-1].timestamp >= end - timedelta(days=1)
    if first is None:
        state = SetupState.EXPIRED if expired else SetupState.NOT_ACTIVE_YET
        return TriggerEvaluation(True, False, False, False, False, None, None, None, expired, "Entry condition not reached.", state)
    post = bars[first:]
    sign = Decimal(1) if prediction.direction == Direction.LONG else Decimal(-1)
    favorable = [((bar.high if sign > 0 else bar.low) - level) / level * sign for bar in post]
    adverse = [((bar.low if sign > 0 else bar.high) - level) / level * sign for bar in post]
    stop = prediction.stop_level
    if stop is None and prediction.stop_invalidation:
        try: stop = Decimal(prediction.stop_invalidation)
        except (InvalidOperation, ValueError): stop = None
    stop_hit = None if stop is None else any(bar.low <= stop if sign > 0 else bar.high >= stop for bar in post)
    target = lambda value: None if value is None else any(bar.high >= value if sign > 0 else bar.low <= value for bar in post)
    tp1_hit, tp2_hit = target(prediction.tp1), target(prediction.tp2)
    stop_index = next((i for i, bar in enumerate(post) if stop is not None and (bar.low <= stop if sign > 0 else bar.high >= stop)), None)
    tp1_index = next((i for i, bar in enumerate(post) if prediction.tp1 is not None and (bar.high >= prediction.tp1 if sign > 0 else bar.low <= prediction.tp1)), None)
    tp2_index = next((i for i, bar in enumerate(post) if prediction.tp2 is not None and (bar.high >= prediction.tp2 if sign > 0 else bar.low <= prediction.tp2)), None)
    timing = None if stop_index is None and tp1_index is None or stop_index == tp1_index else tp1_index is not None and (stop_index is None or tp1_index < stop_index)
    ambiguous = stop_index is not None and (stop_index == tp1_index or stop_index == tp2_index)
    terminal_stop = stop_index is not None and (tp2_index is None or stop_index < tp2_index)
    terminal_target = tp2_index is not None and (stop_index is None or tp2_index < stop_index)
    state = SetupState.INVALIDATED if terminal_stop and not ambiguous else SetupState.COMPLETED if terminal_target and not ambiguous else SetupState.EXPIRED if expired and not ambiguous else SetupState.ACTIVE
    note = "Same-bar stop/target ordering unknown." if ambiguous else "Evaluated from timestamped OHLCV bars."
    return TriggerEvaluation(True, True, stop_hit, tp1_hit, tp2_hit, (post[-1].close - level) / level * sign, min(adverse), max(favorable), expired, note, state, timing, post[0].timestamp)


def build_outcome(prediction: Prediction, provider: MarketDataProvider, now: datetime | None = None) -> Outcome:
    result = evaluate_trigger(prediction, provider, now)
    if not result.available:
        return Outcome(prediction_id=prediction.prediction_id, actual_result="UNAVAILABLE", outcome_status="PENDING", notes=result.notes, data_quality="UNAVAILABLE")
    if result.activated is False:
        status = "EXPIRED" if result.horizon_expired else "PENDING"
        return Outcome(prediction_id=prediction.prediction_id, actual_result="UNTRIGGERED", trigger_activated=False, execution_occurred=False, horizon_expired=result.horizon_expired, outcome_status=status, setup_state=result.setup_state, notes=result.notes, data_quality="AVAILABLE")
    final = result.setup_state in (SetupState.INVALIDATED, SetupState.COMPLETED, SetupState.EXPIRED) and "unknown" not in result.notes.lower()
    return Outcome(prediction_id=prediction.prediction_id, actual_result="EVALUATED", trigger_activated=True, activated_at=result.activated_at, execution_occurred=True if prediction.execution_state == ExecutionState.EXECUTED else None, thesis_correct=(result.realized_move > 0) if final and result.realized_move is not None else None, timing_correct=result.timing_correct, realized_move=result.realized_move, maximum_adverse_excursion=result.mae, maximum_favorable_excursion=result.mfe, stop_hit=result.stop_hit, tp1_hit=result.tp1_hit, tp2_hit=result.tp2_hit, horizon_expired=result.horizon_expired, outcome_status="COMPLETED" if final else "PENDING", setup_state=result.setup_state, evaluation_confidence=1 if final else .5, notes=result.notes, data_quality="AVAILABLE")
