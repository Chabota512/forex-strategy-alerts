"""Causal, read-only shadow features for replay and local analysis."""

from __future__ import annotations

from collections import deque
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Mapping

from .engine import Candle

DC_K_DEFAULT = Decimal("1.0")
DC_ATR_PERIOD = 14
HIER_NEIGHBORS = 2
HIER_DEPTHS = (1, 2, 3)
ZERO = Decimal("0")

Direction = Literal["high", "low"]
TrendState = Literal["undefined", "uptrend", "downtrend", "transition"]


def _positive_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > ZERO else None


class StreamingSimpleATR:
    """Simple-mean true range matching the replay simulator's ATR."""

    def __init__(self, period: int = DC_ATR_PERIOD):
        if period < 1:
            raise ValueError("ATR period must be positive.")
        self.period = period
        self.true_ranges: deque[Decimal] = deque(maxlen=period)
        self.previous_close: Decimal | None = None

    def update(self, candle: Candle) -> Decimal:
        if self.previous_close is None:
            true_range = candle.high - candle.low
        else:
            true_range = max(
                candle.high - candle.low,
                abs(candle.high - self.previous_close),
                abs(candle.low - self.previous_close),
            )
        self.true_ranges.append(true_range)
        self.previous_close = candle.close
        return sum(self.true_ranges, ZERO) / Decimal(len(self.true_ranges))


class StreamingDirectionalChange:
    """ATR-scaled directional change using only closed candles and frozen legs."""

    def __init__(
        self,
        *,
        k: Decimal = DC_K_DEFAULT,
    ):
        parsed_k = _positive_decimal(k)
        if parsed_k is None:
            raise ValueError("DC k must be a finite positive number.")
        self.k = parsed_k
        self.index = -1
        self.state: TrendState = "undefined"
        self.candidate_high: Decimal | None = None
        self.candidate_low: Decimal | None = None
        self.candidate_high_index = -1
        self.candidate_low_index = -1
        self.high_threshold: Decimal | None = None
        self.low_threshold: Decimal | None = None
        self.previous_close: Decimal | None = None
        self.last_confirmed_swing: dict[str, Any] | None = None

    def update(self, candle: Candle, atr14: Any) -> TrendState:
        self.index += 1
        self.last_confirmed_swing = None
        atr = _positive_decimal(atr14)
        if self.index == 0:
            self.candidate_high = candle.high
            self.candidate_low = candle.low
            self.candidate_high_index = self.index
            self.candidate_low_index = self.index
            self.high_threshold = atr * self.k if atr is not None else None
            self.low_threshold = atr * self.k if atr is not None else None
            self.previous_close = candle.close
            return self.state

        if self.state == "undefined":
            if self.candidate_high is None or candle.high > self.candidate_high:
                self.candidate_high = candle.high
                self.candidate_high_index = self.index
            if self.candidate_low is None or candle.low < self.candidate_low:
                self.candidate_low = candle.low
                self.candidate_low_index = self.index
            confirms_down = (
                self.candidate_high is not None
                and self.high_threshold is not None
                and self.candidate_high - candle.close >= self.high_threshold
            )
            confirms_up = (
                self.candidate_low is not None
                and self.low_threshold is not None
                and candle.close - self.candidate_low >= self.low_threshold
            )
            if confirms_up and confirms_down:
                # OHLC cannot disclose the order of both intrabar extremes.
                # Resolve a rare double crossing by the closed-candle direction.
                if self.previous_close is not None and candle.close >= self.previous_close:
                    confirms_down = False
                else:
                    confirms_up = False
            if confirms_up:
                self.last_confirmed_swing = {
                    "direction": "low",
                    "pivot_index": self.candidate_low_index,
                    "price": self.candidate_low,
                }
                self.state = "uptrend"
                self.candidate_high = candle.high
                self.candidate_high_index = self.index
                self.high_threshold = atr * self.k if atr is not None else None
            elif confirms_down:
                self.last_confirmed_swing = {
                    "direction": "high",
                    "pivot_index": self.candidate_high_index,
                    "price": self.candidate_high,
                }
                self.state = "downtrend"
                self.candidate_low = candle.low
                self.candidate_low_index = self.index
                self.low_threshold = atr * self.k if atr is not None else None
        elif self.state == "uptrend":
            if self.candidate_high is None or candle.high > self.candidate_high:
                self.candidate_high = candle.high
                self.candidate_high_index = self.index
            if (
                self.candidate_high is not None
                and self.high_threshold is not None
                and self.candidate_high - candle.close >= self.high_threshold
            ):
                self.last_confirmed_swing = {
                    "direction": "high",
                    "pivot_index": self.candidate_high_index,
                    "price": self.candidate_high,
                }
                self.state = "downtrend"
                self.candidate_low = candle.low
                self.candidate_low_index = self.index
                self.low_threshold = atr * self.k if atr is not None else None
        else:
            if self.candidate_low is None or candle.low < self.candidate_low:
                self.candidate_low = candle.low
                self.candidate_low_index = self.index
            if (
                self.candidate_low is not None
                and self.low_threshold is not None
                and candle.close - self.candidate_low >= self.low_threshold
            ):
                self.last_confirmed_swing = {
                    "direction": "low",
                    "pivot_index": self.candidate_low_index,
                    "price": self.candidate_low,
                }
                self.state = "uptrend"
                self.candidate_high = candle.high
                self.candidate_high_index = self.index
                self.high_threshold = atr * self.k if atr is not None else None

        self.previous_close = candle.close
        return self.state


class StreamingHierarchicalSwings:
    """Promote strict 2-neighbor pivots without backdating discovery."""

    def __init__(self):
        self.index = -1
        self.window: deque[Candle] = deque(maxlen=2 * HIER_NEIGHBORS + 1)
        self.pivots: dict[int, dict[Direction, deque[tuple[int, Decimal]]]] = {
            depth: {
                "high": deque(maxlen=3),
                "low": deque(maxlen=3),
            }
            for depth in HIER_DEPTHS
        }
        self.last_values: dict[tuple[int, Direction], Decimal] = {}
        self.relations: dict[int, dict[Direction, str | None]] = {
            depth: {"high": None, "low": None} for depth in HIER_DEPTHS
        }
        self.depth_states: dict[int, TrendState] = {
            depth: "undefined" for depth in HIER_DEPTHS
        }
        self.state: TrendState = "undefined"

    def update(self, candle: Candle) -> tuple[TrendState, list[dict[str, Any]]]:
        self.index += 1
        self.window.append(candle)
        if len(self.window) < 2 * HIER_NEIGHBORS + 1:
            return self.state, []

        window = list(self.window)
        center = HIER_NEIGHBORS
        pivot_index = self.index - HIER_NEIGHBORS
        pivot = window[center]
        highs = [item.high for offset, item in enumerate(window) if offset != center]
        lows = [item.low for offset, item in enumerate(window) if offset != center]
        candidates: list[tuple[Direction, int, Decimal]] = []
        if all(pivot.high > value for value in highs):
            candidates.append(("high", pivot_index, pivot.high))
        if all(pivot.low < value for value in lows):
            candidates.append(("low", pivot_index, pivot.low))

        promotions: list[dict[str, Any]] = []
        for direction, source_index, value in candidates:
            self._register_pivot(
                depth=1,
                direction=direction,
                pivot=(source_index, value),
                discovery_index=self.index,
                promotions=promotions,
            )
        if promotions:
            promotions.sort(key=lambda event: (event["depth"], event["direction"]))
            deepest_available = max(
                (
                    depth
                    for depth, state in self.depth_states.items()
                    if state != "undefined"
                ),
                default=None,
            )
            self.state = (
                self.depth_states[deepest_available]
                if deepest_available is not None
                else "undefined"
            )
        return self.state, promotions

    def _register_pivot(
        self,
        *,
        depth: int,
        direction: Direction,
        pivot: tuple[int, Decimal],
        discovery_index: int,
        promotions: list[dict[str, Any]],
    ) -> None:
        sequence = self.pivots[depth][direction]
        sequence.append(pivot)
        if len(sequence) < 3:
            return
        left, middle, right = sequence
        value = middle[1]
        dominates = (
            value > left[1] and value > right[1]
            if direction == "high"
            else value < left[1] and value < right[1]
        )
        if not dominates:
            return
        event = {
            "depth": depth,
            "direction": direction,
            "pivot_index": middle[0],
            "discovered_at": discovery_index,
        }
        promotions.append(event)
        previous_value = self.last_values.get((depth, direction))
        if previous_value is not None:
            self.relations[depth][direction] = (
                "higher"
                if value > previous_value
                else "lower"
                if value < previous_value
                else "equal"
            )
            self.depth_states[depth] = self._state_for_depth(depth)
        self.last_values[(depth, direction)] = value
        if depth < HIER_DEPTHS[-1]:
            self._register_pivot(
                depth=depth + 1,
                direction=direction,
                pivot=middle,
                discovery_index=discovery_index,
                promotions=promotions,
            )

    def _state_for_depth(self, depth: int) -> TrendState:
        high_relation = self.relations[depth]["high"]
        low_relation = self.relations[depth]["low"]
        if high_relation is None or low_relation is None:
            return "undefined"
        if high_relation == low_relation == "higher":
            return "uptrend"
        if high_relation == low_relation == "lower":
            return "downtrend"
        return "transition"


def compute_shadow_states(candles: list[Candle]) -> list[dict[str, Any]]:
    """Return batch results from the same incremental detector implementations."""
    atr = StreamingSimpleATR()
    dc = StreamingDirectionalChange()
    hier = StreamingHierarchicalSwings()
    states: list[dict[str, Any]] = []
    for candle in candles:
        atr14 = atr.update(candle)
        dc_state = dc.update(candle, atr14)
        hier_state, promotions = hier.update(candle)
        states.append(
            {
                "dc_state": dc_state,
                "hier_state": hier_state,
                "hier_promotions": promotions,
            }
        )
    return states


def snapshot_shadow_fields(
    states: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "dc_state": states.get("dc_state", "undefined"),
        "hier_state": states.get("hier_state", "undefined"),
        "hier_promotions": list(states.get("hier_promotions", [])),
    }
