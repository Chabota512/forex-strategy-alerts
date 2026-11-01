"""Pure, deterministic candle rules for Strategy Lab Phase 1."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Mapping, Sequence

Direction = Literal["BUY", "SELL"]
VariantBAlertType = Literal["BUY-B", "SELL-B"]
Trend = Literal["uptrend", "downtrend", "ranging"]
CSTState = Literal["uptrend", "downtrend", "transition", "ranging", "undefined"]
SwingDirectionState = Literal["uptrend", "downtrend", "consolidation"]
Strength = Literal["STRONG", "NEUTRAL", "WEAK"]
WickPressure = Literal["up-pressure", "balanced", "down-pressure"]
ZERO = Decimal("0")
# Smaller-body note ratio, quantified from owner-labeled fixtures — pending owner confirmation.
SMALLER_BODY_NOTE_RATIO = Decimal("0.5")
WICK_PRESSURE_WINDOW = 20
WICK_PRESSURE_BALANCE_THRESHOLD = Decimal("0.10")
STRENGTH_FEATURES = (
    "cst_alignment",
    "cct_alignment",
    "wp_agree",
    "engulf",
    "mtf_trend_agree",
    "mtf_wp_agree",
    "dual_tf_alert",
)
SIGNED_STRENGTH_FEATURES = frozenset(
    {"cst_alignment", "cct_alignment", "wp_agree", "mtf_trend_agree", "mtf_wp_agree"}
)
DEFAULT_STRENGTH_WEIGHTS: dict[str, Decimal] = {
    feature: Decimal("1") / Decimal(len(STRENGTH_FEATURES))
    for feature in STRENGTH_FEATURES
}
DEFAULT_STRENGTH_THRESHOLDS: dict[str, Decimal] = {
    "strong_min": Decimal("0.58"),
    "neutral_min": Decimal("0.42"),
}


class CandleDataError(ValueError):
    """Raised when a candle cannot represent valid OHLC data."""


@dataclass(frozen=True)
class Candle:
    time: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    @classmethod
    def from_values(
        cls,
        time: str,
        open_: str | Decimal,
        high: str | Decimal,
        low: str | Decimal,
        close: str | Decimal,
    ) -> "Candle":
        try:
            candle = cls(
                time=time,
                open=Decimal(open_),
                high=Decimal(high),
                low=Decimal(low),
                close=Decimal(close),
            )
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise CandleDataError(f"Invalid OHLC value at {time!r}") from exc
        validate_candle(candle)
        return candle


def validate_candle(candle: Candle) -> None:
    values = (candle.open, candle.high, candle.low, candle.close)
    if not all(value.is_finite() for value in values):
        raise CandleDataError(f"Non-finite OHLC value at {candle.time!r}")
    if any(value <= ZERO for value in values):
        raise CandleDataError(f"Prices must be positive at {candle.time!r}")
    if candle.high < max(candle.open, candle.close):
        raise CandleDataError(f"High is below open or close at {candle.time!r}")
    if candle.low > min(candle.open, candle.close):
        raise CandleDataError(f"Low is above open or close at {candle.time!r}")
    if candle.high < candle.low:
        raise CandleDataError(f"High is below low at {candle.time!r}")


def _wick_contribution(candle: Candle) -> tuple[Decimal, Decimal]:
    candle_range = candle.high - candle.low
    if candle_range <= ZERO:
        return ZERO, ZERO
    lower = (min(candle.open, candle.close) - candle.low) / candle_range
    upper = (candle.high - max(candle.open, candle.close)) / candle_range
    return lower, upper


def _wick_pressure_from_sums(
    lower_sum: Decimal,
    upper_sum: Decimal,
    balance_threshold: Decimal = WICK_PRESSURE_BALANCE_THRESHOLD,
) -> WickPressure:
    total = lower_sum + upper_sum
    if total == ZERO:
        return "balanced"
    relative_difference = (lower_sum - upper_sum) / total
    if relative_difference > balance_threshold:
        return "up-pressure"
    if relative_difference < -balance_threshold:
        return "down-pressure"
    return "balanced"


def compute_wick_pressure_states(
    candles: list[Candle],
    *,
    window_size: int = WICK_PRESSURE_WINDOW,
    balance_threshold: Decimal = WICK_PRESSURE_BALANCE_THRESHOLD,
) -> list[WickPressure]:
    """Return causal rolling wick-pressure states for each supplied candle."""
    if window_size < 1:
        raise ValueError("window_size must be positive")
    if (
        not balance_threshold.is_finite()
        or balance_threshold < ZERO
        or balance_threshold >= Decimal("1")
    ):
        raise ValueError("balance_threshold must be finite and within [0, 1)")
    contributions: deque[tuple[Decimal, Decimal]] = deque()
    lower_sum = ZERO
    upper_sum = ZERO
    states: list[WickPressure] = []
    for candle in candles:
        lower, upper = _wick_contribution(candle)
        if len(contributions) >= window_size:
            old_lower, old_upper = contributions.popleft()
            lower_sum -= old_lower
            upper_sum -= old_upper
        contributions.append((lower, upper))
        lower_sum += lower
        upper_sum += upper
        states.append(_wick_pressure_from_sums(lower_sum, upper_sum, balance_threshold))
    return states


def detect_trigger(previous: Candle, current: Candle) -> Direction | None:
    """Apply the v1.5 strict wick-break trigger to two fully closed candles."""
    previous_bullish = previous.close > previous.open
    previous_bearish = previous.close < previous.open
    current_bullish = current.close > current.open
    current_bearish = current.close < current.open

    if current_bearish and previous_bullish and current.high > previous.high:
        return "SELL"
    if current_bullish and previous_bearish and current.low < previous.low:
        return "BUY"
    return None


def detect_trigger_b(
    previous: Candle, current: Candle
) -> VariantBAlertType | None:
    """Evaluate the always-on same-color sweep without changing the v1.5 trigger."""
    previous_bullish = previous.close > previous.open
    previous_bearish = previous.close < previous.open
    current_bullish = current.close > current.open
    current_bearish = current.close < current.open

    if current_bearish and previous_bearish and current.high > previous.high:
        return "SELL-B"
    if current_bullish and previous_bullish and current.low < previous.low:
        return "BUY-B"
    return None


def body_engulfs_previous(
    previous: Candle, current: Candle, direction: Direction
) -> bool:
    """Return whether the current candle body engulfs the previous body."""
    if direction == "BUY":
        return current.open <= previous.close and current.close >= previous.open
    return current.open >= previous.close and current.close <= previous.open


def smaller_body_note(previous: Candle, current: Candle) -> str | None:
    """Note a current body strictly less than half the previous body."""
    previous_body = abs(previous.close - previous.open)
    current_body = abs(current.close - current.open)
    if current_body < previous_body * SMALLER_BODY_NOTE_RATIO:
        return "smaller body / weaker follow-through"
    return None


def _is_swing_high(candles: list[Candle], index: int, neighbors: int) -> bool:
    value = candles[index].high
    others = (
        candles[index - offset].high
        for offset in range(neighbors, 0, -1)
    )
    right = (candles[index + offset].high for offset in range(1, neighbors + 1))
    return all(value > other for other in (*others, *right))


def _is_swing_low(candles: list[Candle], index: int, neighbors: int) -> bool:
    value = candles[index].low
    others = (
        candles[index - offset].low
        for offset in range(neighbors, 0, -1)
    )
    right = (candles[index + offset].low for offset in range(1, neighbors + 1))
    return all(value < other for other in (*others, *right))


def classify_cct_legacy(
    closed_candles: list[Candle],
    *,
    window: int = 15,
    swing_neighbors: int = 2,
) -> Trend:
    """Classify the descriptive CCT using only the supplied closed-candle window."""
    if window < 1 or swing_neighbors < 1:
        raise ValueError("window and swing_neighbors must be positive")
    candles = closed_candles[-window:]
    if len(candles) < 2 * swing_neighbors + 1:
        return "ranging"

    swing_highs = [
        candles[index].high
        for index in range(swing_neighbors, len(candles) - swing_neighbors)
        if _is_swing_high(candles, index, swing_neighbors)
    ]
    swing_lows = [
        candles[index].low
        for index in range(swing_neighbors, len(candles) - swing_neighbors)
        if _is_swing_low(candles, index, swing_neighbors)
    ]
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "ranging"

    higher_highs = swing_highs[-1] > swing_highs[-2]
    higher_lows = swing_lows[-1] > swing_lows[-2]
    lower_highs = swing_highs[-1] < swing_highs[-2]
    lower_lows = swing_lows[-1] < swing_lows[-2]
    if higher_highs and higher_lows:
        return "uptrend"
    if lower_highs and lower_lows:
        return "downtrend"
    return "ranging"


@dataclass(frozen=True)
class AmplitudeSwing:
    direction: Literal["high", "low"]
    pivot_index: int
    price: Decimal
    confirmed_index: int
    strength_atr: Decimal


class StreamingAmplitudeSwings:
    """Confirm directional-change pivots using only closed candles and ATR(14)."""

    def __init__(self, *, k: Decimal | float = Decimal("1"), atr_period: int = 14):
        try:
            parsed_k = Decimal(str(k))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("k must be a positive finite number.") from exc
        if not parsed_k.is_finite() or parsed_k <= ZERO:
            raise ValueError("k must be a positive finite number.")
        if atr_period < 1:
            raise ValueError("atr_period must be positive.")
        self.k = parsed_k
        self.atr_period = atr_period
        self.true_ranges: deque[Decimal] = deque(maxlen=atr_period)
        self.previous_close: Decimal | None = None
        self.index = -1
        self.ready = False
        self.direction: Literal["up", "down"] | None = None
        self.candidate_high: tuple[int, Decimal] | None = None
        self.candidate_low: tuple[int, Decimal] | None = None
        self.initial_high: Decimal | None = None
        self.initial_low: Decimal | None = None
        self.swing_highs: list[AmplitudeSwing] = []
        self.swing_lows: list[AmplitudeSwing] = []
        self.last_confirmed_swings: list[AmplitudeSwing] = []

    @property
    def atr(self) -> Decimal | None:
        if len(self.true_ranges) < self.atr_period:
            return None
        return sum(self.true_ranges, ZERO) / Decimal(self.atr_period)

    def _confirm(
        self,
        direction: Literal["high", "low"],
        candle: Candle,
        atr: Decimal,
    ) -> AmplitudeSwing:
        candidate = self.candidate_high if direction == "high" else self.candidate_low
        if candidate is None:
            raise RuntimeError("Cannot confirm an amplitude swing without a pivot.")
        pivot_index, price = candidate
        if direction == "high":
            origin = (
                self.swing_lows[-1].price
                if self.swing_lows
                else self.initial_low
            )
        else:
            origin = (
                self.swing_highs[-1].price
                if self.swing_highs
                else self.initial_high
            )
        if origin is None:
            origin = price
        strength = abs(price - origin) / atr if atr > ZERO else ZERO
        swing = AmplitudeSwing(
            direction=direction,
            pivot_index=pivot_index,
            price=price,
            confirmed_index=self.index,
            strength_atr=strength,
        )
        if direction == "high":
            self.swing_highs.append(swing)
            self.direction = "down"
            self.candidate_high = None
            self.candidate_low = (self.index, candle.low)
        else:
            self.swing_lows.append(swing)
            self.direction = "up"
            self.candidate_low = None
            self.candidate_high = (self.index, candle.high)
        self.last_confirmed_swings.append(swing)
        return swing

    def update(self, candle: Candle) -> Decimal | None:
        self.index += 1
        self.last_confirmed_swings = []
        previous_close = self.previous_close
        true_range = candle.high - candle.low
        if previous_close is not None:
            true_range = max(
                true_range,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        self.true_ranges.append(true_range)
        self.previous_close = candle.close
        atr = self.atr
        if atr is None:
            return None
        if not self.ready:
            self.ready = True
            self.initial_high = candle.high
            self.initial_low = candle.low
            self.candidate_high = (self.index, candle.high)
            self.candidate_low = (self.index, candle.low)
            return atr

        threshold = self.k * atr
        if self.direction == "up":
            if self.candidate_high is None or candle.high > self.candidate_high[1]:
                self.candidate_high = (self.index, candle.high)
            if self.candidate_high[1] - candle.close >= threshold:
                self._confirm("high", candle, atr)
        elif self.direction == "down":
            if self.candidate_low is None or candle.low < self.candidate_low[1]:
                self.candidate_low = (self.index, candle.low)
            if candle.close - self.candidate_low[1] >= threshold:
                self._confirm("low", candle, atr)
        else:
            if self.candidate_high is None or candle.high > self.candidate_high[1]:
                self.candidate_high = (self.index, candle.high)
            if self.candidate_low is None or candle.low < self.candidate_low[1]:
                self.candidate_low = (self.index, candle.low)
            high_retrace = self.candidate_high[1] - candle.close >= threshold
            low_retrace = candle.close - self.candidate_low[1] >= threshold
            if high_retrace and low_retrace:
                if previous_close is not None and candle.close < previous_close:
                    self._confirm("high", candle, atr)
                elif previous_close is not None and candle.close > previous_close:
                    self._confirm("low", candle, atr)
            elif high_retrace:
                self._confirm("high", candle, atr)
            elif low_retrace:
                self._confirm("low", candle, atr)
        return atr


class StreamingAmplitudeCCT:
    """Fast CCT state from the latest two confirmed ATR-amplitude swings."""

    def __init__(
        self,
        *,
        window: int = 15,
        k_fast: Decimal | float = Decimal("1"),
        atr_period: int = 14,
    ):
        if window < 1:
            raise ValueError("window must be positive")
        self.window = window
        self.swings = StreamingAmplitudeSwings(k=k_fast, atr_period=atr_period)
        self.state: Trend = "ranging"
        self.last_confirmed_swings: list[AmplitudeSwing] = []

    def update(self, candle: Candle) -> Trend:
        self.swings.update(candle)
        self.last_confirmed_swings = list(self.swings.last_confirmed_swings)
        index = self.swings.index
        earliest = max(0, index - self.window + 1)
        highs = [
            swing for swing in self.swings.swing_highs
            if swing.pivot_index >= earliest
        ]
        lows = [
            swing for swing in self.swings.swing_lows
            if swing.pivot_index >= earliest
        ]
        self.state = "ranging"
        if len(highs) >= 2 and len(lows) >= 2:
            higher_highs = highs[-1].price > highs[-2].price
            higher_lows = lows[-1].price > lows[-2].price
            lower_highs = highs[-1].price < highs[-2].price
            lower_lows = lows[-1].price < lows[-2].price
            if higher_highs and higher_lows:
                self.state = "uptrend"
            elif lower_highs and lower_lows:
                self.state = "downtrend"
        return self.state


def compute_cct_states(
    candles: list[Candle],
    *,
    window: int = 15,
    k_fast: Decimal | float = Decimal("1"),
    atr_period: int = 14,
) -> list[Trend]:
    stream = StreamingAmplitudeCCT(
        window=window,
        k_fast=k_fast,
        atr_period=atr_period,
    )
    return [stream.update(candle) for candle in candles]


def classify_cct(
    closed_candles: list[Candle],
    *,
    window: int = 15,
    k_fast: Decimal | float = Decimal("1"),
    atr_period: int = 14,
    swing_neighbors: int | None = None,
) -> Trend:
    """Classify the fast CCT from causal ATR-amplitude swings."""
    if swing_neighbors is not None and swing_neighbors < 1:
        raise ValueError("swing_neighbors must be positive")
    if not closed_candles:
        return "ranging"
    return compute_cct_states(
        closed_candles,
        window=window,
        k_fast=k_fast,
        atr_period=atr_period,
    )[-1]


def _breaks_level(candle: Candle, level: Decimal, direction: str, basis: str) -> bool:
    if basis == "close":
        return candle.close > level if direction == "up" else candle.close < level
    if basis == "wick":
        return candle.high > level if direction == "up" else candle.low < level
    raise ValueError("break_basis must be 'close' or 'wick'")


def compute_cst_legacy(
    candles: list[Candle],
    *,
    swing_neighbors: int = 5,
    break_basis: str = "close",
) -> list[CSTState]:
    """Compute CST one closed candle at a time without using unconfirmed pivots.

    A post-CHoCH swing in the new direction supplies the confirmation level.
    The prior trend resumes if its saved major level breaks first. If wick mode
    breaks both levels in the same candle, the state stays in transition.
    """
    if swing_neighbors < 1:
        raise ValueError("swing_neighbors must be positive")
    if break_basis not in {"close", "wick"}:
        raise ValueError("break_basis must be 'close' or 'wick'")

    state: CSTState = "undefined"
    old_trend: Literal["uptrend", "downtrend"] | None = None
    transition_start = -1
    resume_level: Decimal | None = None
    confirmation_level: Decimal | None = None
    swing_highs: list[tuple[int, Decimal]] = []
    swing_lows: list[tuple[int, Decimal]] = []
    states: list[CSTState] = []

    for index, candle in enumerate(candles):
        pivot_index = index - swing_neighbors
        new_high: tuple[int, Decimal] | None = None
        new_low: tuple[int, Decimal] | None = None
        if pivot_index >= swing_neighbors:
            if _is_swing_high(candles, pivot_index, swing_neighbors):
                new_high = (pivot_index, candles[pivot_index].high)
                swing_highs.append(new_high)
            if _is_swing_low(candles, pivot_index, swing_neighbors):
                new_low = (pivot_index, candles[pivot_index].low)
                swing_lows.append(new_low)

        last_high = swing_highs[-1][1] if swing_highs else None
        last_low = swing_lows[-1][1] if swing_lows else None
        initiated_transition = False

        if state == "undefined":
            broke_up = last_high is not None and _breaks_level(
                candle, last_high, "up", break_basis
            )
            broke_down = last_low is not None and _breaks_level(
                candle, last_low, "down", break_basis
            )
            if broke_up and not broke_down:
                state = "uptrend"
            elif broke_down and not broke_up:
                state = "downtrend"
        elif state == "uptrend" and last_low is not None:
            if _breaks_level(candle, last_low, "down", break_basis):
                state = "transition"
                old_trend = "uptrend"
                transition_start = index
                resume_level = last_high
                confirmation_level = None
                initiated_transition = True
        elif state == "downtrend" and last_high is not None:
            if _breaks_level(candle, last_high, "up", break_basis):
                state = "transition"
                old_trend = "downtrend"
                transition_start = index
                resume_level = last_low
                confirmation_level = None
                initiated_transition = True

        if state == "transition" and not initiated_transition:
            if old_trend == "uptrend" and new_low and new_low[0] > transition_start:
                confirmation_level = new_low[1]
            elif old_trend == "downtrend" and new_high and new_high[0] > transition_start:
                confirmation_level = new_high[1]

            if old_trend == "uptrend":
                resumes_old = (
                    resume_level is not None
                    and _breaks_level(candle, resume_level, "up", break_basis)
                )
                confirms_new = (
                    confirmation_level is not None
                    and _breaks_level(candle, confirmation_level, "down", break_basis)
                )
                if resumes_old and not confirms_new:
                    state = "uptrend"
                elif confirms_new and not resumes_old:
                    state = "downtrend"
            elif old_trend == "downtrend":
                resumes_old = (
                    resume_level is not None
                    and _breaks_level(candle, resume_level, "down", break_basis)
                )
                confirms_new = (
                    confirmation_level is not None
                    and _breaks_level(candle, confirmation_level, "up", break_basis)
                )
                if resumes_old and not confirms_new:
                    state = "downtrend"
                elif confirms_new and not resumes_old:
                    state = "uptrend"

            if state != "transition":
                old_trend = None
                transition_start = -1
                resume_level = None
                confirmation_level = None

        states.append(state)
    return states


class StreamingAmplitudeCST:
    """Structural CST state machine driven by confirmed amplitude swings."""

    def __init__(
        self,
        *,
        k_struct: Decimal | float = Decimal("2.5"),
        break_basis: str = "close",
        atr_period: int = 14,
    ):
        if break_basis not in {"close", "wick"}:
            raise ValueError("break_basis must be 'close' or 'wick'")
        self.break_basis = break_basis
        self.swings = StreamingAmplitudeSwings(k=k_struct, atr_period=atr_period)
        self.state: CSTState = "undefined"
        self.old_trend: Literal["uptrend", "downtrend"] | None = None
        self.transition_start = -1
        self.resume_level: Decimal | None = None
        self.confirmation_level: Decimal | None = None
        self.swing_highs: list[AmplitudeSwing] = []
        self.swing_lows: list[AmplitudeSwing] = []
        self.last_confirmed_swings: list[AmplitudeSwing] = []

    def update(self, candle: Candle) -> CSTState:
        atr = self.swings.update(candle)
        self.last_confirmed_swings = list(self.swings.last_confirmed_swings)
        if atr is None:
            return self.state
        if self.state == "undefined":
            self.state = "ranging"

        new_high: AmplitudeSwing | None = None
        new_low: AmplitudeSwing | None = None
        for swing in self.last_confirmed_swings:
            if swing.direction == "high":
                self.swing_highs.append(swing)
                new_high = swing
            else:
                self.swing_lows.append(swing)
                new_low = swing
        last_high = self.swing_highs[-1].price if self.swing_highs else None
        last_low = self.swing_lows[-1].price if self.swing_lows else None
        initiated_transition = False

        if self.state == "ranging":
            broke_up = last_high is not None and _breaks_level(
                candle, last_high, "up", self.break_basis
            )
            broke_down = last_low is not None and _breaks_level(
                candle, last_low, "down", self.break_basis
            )
            if broke_up and not broke_down:
                self.state = "uptrend"
            elif broke_down and not broke_up:
                self.state = "downtrend"
        elif self.state == "uptrend" and last_low is not None:
            if _breaks_level(candle, last_low, "down", self.break_basis):
                self.state = "transition"
                self.old_trend = "uptrend"
                self.transition_start = self.swings.index
                self.resume_level = last_high
                self.confirmation_level = None
                initiated_transition = True
        elif self.state == "downtrend" and last_high is not None:
            if _breaks_level(candle, last_high, "up", self.break_basis):
                self.state = "transition"
                self.old_trend = "downtrend"
                self.transition_start = self.swings.index
                self.resume_level = last_low
                self.confirmation_level = None
                initiated_transition = True

        if self.state == "transition" and not initiated_transition:
            if (
                self.old_trend == "uptrend"
                and new_low is not None
                and new_low.pivot_index > self.transition_start
            ):
                self.confirmation_level = new_low.price
            elif (
                self.old_trend == "downtrend"
                and new_high is not None
                and new_high.pivot_index > self.transition_start
            ):
                self.confirmation_level = new_high.price

            if self.old_trend == "uptrend":
                resumes_old = (
                    self.resume_level is not None
                    and _breaks_level(candle, self.resume_level, "up", self.break_basis)
                )
                confirms_new = (
                    self.confirmation_level is not None
                    and _breaks_level(
                        candle, self.confirmation_level, "down", self.break_basis
                    )
                )
                if resumes_old and not confirms_new:
                    self.state = "uptrend"
                elif confirms_new and not resumes_old:
                    self.state = "downtrend"
            elif self.old_trend == "downtrend":
                resumes_old = (
                    self.resume_level is not None
                    and _breaks_level(candle, self.resume_level, "down", self.break_basis)
                )
                confirms_new = (
                    self.confirmation_level is not None
                    and _breaks_level(
                        candle, self.confirmation_level, "up", self.break_basis
                    )
                )
                if resumes_old and not confirms_new:
                    self.state = "downtrend"
                elif confirms_new and not resumes_old:
                    self.state = "uptrend"

            if self.state != "transition":
                self.old_trend = None
                self.transition_start = -1
                self.resume_level = None
                self.confirmation_level = None
        return self.state


def compute_cst(
    candles: list[Candle],
    *,
    k_struct: Decimal | float = Decimal("2.5"),
    break_basis: str = "close",
    atr_period: int = 14,
) -> list[CSTState]:
    """Compute structural CST from causal amplitude swings."""
    stream = StreamingAmplitudeCST(
        k_struct=k_struct,
        break_basis=break_basis,
        atr_period=atr_period,
    )
    return [stream.update(candle) for candle in candles]


class SwingDirectionV2:
    """Track confirmed major swings and causal same-type reversal overrides."""

    def __init__(self) -> None:
        self.state: SwingDirectionState = "consolidation"
        self.current_mt: Decimal | None = None
        self.current_ml: Decimal | None = None
        self.last_major_type: Literal["high", "low"] | None = None
        self.last_major_price: Decimal | None = None
        self.previous_same_type_price: Decimal | None = None
        self.reversal = False
        self.index = -1

    def update(
        self,
        candle: Candle,
        confirmed_major_swings: Sequence[AmplitudeSwing] = (),
    ) -> SwingDirectionState:
        """Advance from one closed candle and any swings confirmed on that close."""
        self.index += 1
        self.reversal = False
        for swing in confirmed_major_swings:
            if swing.direction == self.last_major_type:
                self.previous_same_type_price = self.last_major_price
            else:
                self.previous_same_type_price = None
            self.last_major_type = swing.direction
            self.last_major_price = swing.price
            if swing.direction == "high":
                self.current_mt = swing.price
            else:
                self.current_ml = swing.price

        if self.current_mt is not None and candle.close > self.current_mt:
            self.state = "uptrend"
        elif self.current_ml is not None and candle.close < self.current_ml:
            self.state = "downtrend"
        if (
            self.last_major_type == "low"
            and self.previous_same_type_price is not None
            and candle.close < self.previous_same_type_price
        ):
            self.state = "uptrend"
            self.reversal = True
        elif (
            self.last_major_type == "high"
            and self.previous_same_type_price is not None
            and candle.close > self.previous_same_type_price
        ):
            self.state = "downtrend"
            self.reversal = True
        return self.state


def compute_cst_versions(
    candles: list[Candle],
    *,
    k_struct: Decimal | float = Decimal("2.5"),
    break_basis: str = "close",
    atr_period: int = 14,
) -> tuple[list[CSTState], list[SwingDirectionState]]:
    """Compute amplitude CST v1 and swing-direction v2 from the same swing stream."""
    v1_states, v2_states, _ = compute_cst_versions_with_reversals(
        candles,
        k_struct=k_struct,
        break_basis=break_basis,
        atr_period=atr_period,
    )
    return v1_states, v2_states


def compute_cst_versions_with_reversals(
    candles: list[Candle],
    *,
    k_struct: Decimal | float = Decimal("2.5"),
    break_basis: str = "close",
    atr_period: int = 14,
) -> tuple[list[CSTState], list[SwingDirectionState], list[bool]]:
    """Compute both swing states and the per-candle V2 reversal marker."""
    v1 = StreamingAmplitudeCST(
        k_struct=k_struct,
        break_basis=break_basis,
        atr_period=atr_period,
    )
    v2 = SwingDirectionV2()
    v1_states: list[CSTState] = []
    v2_states: list[SwingDirectionState] = []
    reversals: list[bool] = []
    for candle in candles:
        v1_states.append(v1.update(candle))
        v2_states.append(v2.update(candle, v1.last_confirmed_swings))
        reversals.append(v2.reversal)
    return v1_states, v2_states, reversals


def swing_version_agreement_pct(
    v1_state: str | None,
    v2_state: str | None,
) -> int | None:
    """Compare directional agreement, treating non-directional states as neutral."""
    if v1_state is None or v2_state is None:
        return None
    v1_direction = v1_state if v1_state in {"uptrend", "downtrend"} else "consolidation"
    v2_direction = v2_state if v2_state in {"uptrend", "downtrend"} else "consolidation"
    return 100 if v1_direction == v2_direction else 0


def _trend_alignment(direction: Direction, state: str | None) -> int:
    if state not in {"uptrend", "downtrend"}:
        return 0
    expected = "uptrend" if direction == "BUY" else "downtrend"
    return 1 if state == expected else -1


def _pressure_alignment(direction: Direction, state: str | None) -> int:
    if state not in {"up-pressure", "down-pressure"}:
        return 0
    expected = "up-pressure" if direction == "BUY" else "down-pressure"
    return 1 if state == expected else -1


def strength_for(direction: Direction, cst_state: str | None) -> Strength:
    """Return the frozen v1.5 strength label derived from CST only."""
    if cst_state not in {"uptrend", "downtrend"}:
        return "NEUTRAL"
    aligned_trend = "uptrend" if direction == "BUY" else "downtrend"
    return "STRONG" if cst_state == aligned_trend else "WEAK"


def build_strength_features(
    direction: Direction,
    *,
    cct: str | None,
    cst: str | None,
    wick_pressure: str | None,
    engulfing: bool,
    mtf_cst: str | None,
    mtf_wick_pressure: str | None,
    dual_tf_alert: bool,
) -> dict[str, int]:
    """Build the seven bounded, causal strength features for one alert."""
    return {
        "cst_alignment": _trend_alignment(direction, cst),
        "cct_alignment": _trend_alignment(direction, cct),
        "wp_agree": _pressure_alignment(direction, wick_pressure),
        "engulf": int(engulfing),
        "mtf_trend_agree": _trend_alignment(direction, mtf_cst),
        "mtf_wp_agree": _pressure_alignment(direction, mtf_wick_pressure),
        "dual_tf_alert": int(dual_tf_alert),
    }


def normalize_strength_weights(
    weights: Mapping[str, Any] | None,
) -> dict[str, Decimal]:
    """Validate signed bounded weights and normalize their absolute sum to one."""
    source = DEFAULT_STRENGTH_WEIGHTS if weights is None else weights
    if not isinstance(source, Mapping) or set(source) != set(STRENGTH_FEATURES):
        raise ValueError("strength_weights must contain exactly the seven supported features.")
    parsed: dict[str, Decimal] = {}
    for feature in STRENGTH_FEATURES:
        value = source[feature]
        if isinstance(value, bool):
            raise ValueError(f"strength_weights.{feature} must be a number from -1 to 1.")
        try:
            weight = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(
                f"strength_weights.{feature} must be a number from -1 to 1."
            ) from exc
        if (
            not weight.is_finite()
            or weight < Decimal("-1")
            or weight > Decimal("1")
        ):
            raise ValueError(f"strength_weights.{feature} must be a number from -1 to 1.")
        parsed[feature] = weight
    total = sum((abs(value) for value in parsed.values()), ZERO)
    if total <= ZERO:
        raise ValueError("strength_weights must have a positive absolute total.")
    normalized = {feature: parsed[feature] / total for feature in STRENGTH_FEATURES}
    # Absorb Decimal division rounding in the last non-zero entry while keeping
    # the original sign and an exact L1 norm of one.
    last_nonzero = next(
        feature for feature in reversed(STRENGTH_FEATURES) if parsed[feature] != ZERO
    )
    preceding = sum(
        (
            abs(normalized[feature])
            for feature in STRENGTH_FEATURES
            if feature != last_nonzero
        ),
        ZERO,
    )
    residual = Decimal("1") - preceding
    normalized[last_nonzero] = residual if parsed[last_nonzero] > ZERO else -residual
    return normalized


def normalize_strength_thresholds(
    thresholds: Mapping[str, Any] | None,
) -> dict[str, Decimal]:
    source = DEFAULT_STRENGTH_THRESHOLDS if thresholds is None else thresholds
    if not isinstance(source, Mapping) or set(source) != {"strong_min", "neutral_min"}:
        raise ValueError(
            "strength_thresholds must contain strong_min and neutral_min."
        )
    parsed: dict[str, Decimal] = {}
    for key in ("strong_min", "neutral_min"):
        value = source[key]
        if isinstance(value, bool):
            raise ValueError(f"strength_thresholds.{key} must be between 0 and 1.")
        try:
            threshold = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(
                f"strength_thresholds.{key} must be between 0 and 1."
            ) from exc
        if not threshold.is_finite() or not ZERO <= threshold <= Decimal("1"):
            raise ValueError(f"strength_thresholds.{key} must be between 0 and 1.")
        parsed[key] = threshold
    if parsed["neutral_min"] >= parsed["strong_min"]:
        raise ValueError("neutral_min must be below strong_min.")
    return parsed


def score_strength(
    features: Mapping[str, Any],
    *,
    weights: Mapping[str, Any] | None = None,
    thresholds: Mapping[str, Any] | None = None,
) -> tuple[Decimal, Strength]:
    """Compute a normalized [0, 1] composite score and its S/N/W label.

    Signed agreement features map from [-1, 1] to [0, 1]; binary features
    remain [0, 1]. A neutral or unavailable agreement feature maps to 0.5.
    Negative weights invert the feature contribution; the weighted sum is
    shifted by its minimum possible value to keep the score in [0, 1].
    """
    if not isinstance(features, Mapping) or set(features) != set(STRENGTH_FEATURES):
        raise ValueError("Strength features do not match the supported feature set.")
    normalized_weights = normalize_strength_weights(weights)
    normalized_thresholds = normalize_strength_thresholds(thresholds)
    weighted_score = ZERO
    score_floor = sum(
        (weight for weight in normalized_weights.values() if weight < ZERO),
        ZERO,
    )
    for feature in STRENGTH_FEATURES:
        raw = features[feature]
        if isinstance(raw, bool):
            raw = int(raw)
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"Strength feature {feature} must be numeric.") from exc
        if not value.is_finite():
            raise ValueError(f"Strength feature {feature} must be finite.")
        if feature in SIGNED_STRENGTH_FEATURES:
            if value not in {Decimal("-1"), ZERO, Decimal("1")}:
                raise ValueError(f"Strength feature {feature} must be -1, 0, or 1.")
            value = (value + Decimal("1")) / Decimal("2")
        elif value not in {ZERO, Decimal("1")}:
            raise ValueError(f"Strength feature {feature} must be 0 or 1.")
        weighted_score += normalized_weights[feature] * value
    score = (weighted_score - score_floor).quantize(Decimal("0.000001"))
    if score >= normalized_thresholds["strong_min"]:
        label: Strength = "STRONG"
    elif score >= normalized_thresholds["neutral_min"]:
        label = "NEUTRAL"
    else:
        label = "WEAK"
    return score, label


def calculate_strength(
    direction: Direction,
    *,
    cct: str | None,
    cst: str | None,
    wick_pressure: str | None,
    engulfing: bool,
    mtf_cst: str | None,
    mtf_wick_pressure: str | None,
    dual_tf_alert: bool,
    weights: Mapping[str, Any] | None = None,
    thresholds: Mapping[str, Any] | None = None,
) -> tuple[Decimal, Strength, dict[str, int]]:
    features = build_strength_features(
        direction,
        cct=cct,
        cst=cst,
        wick_pressure=wick_pressure,
        engulfing=engulfing,
        mtf_cst=mtf_cst,
        mtf_wick_pressure=mtf_wick_pressure,
        dual_tf_alert=dual_tf_alert,
    )
    score, label = score_strength(features, weights=weights, thresholds=thresholds)
    return score, label, features


def score_alert(
    direction: Direction,
    entry_price: Decimal,
    future_candles: list[Candle],
    *,
    d_min: Decimal,
    a_max: Decimal,
    lookahead: int = 12,
    speed_bonus_candles: int = 6,
    same_candle_tie: str = "adverse_first",
    follow_through_per_unit: Decimal = Decimal("0.1"),
    follow_through_cap: Decimal = Decimal("0.5"),
) -> tuple[Decimal | None, str]:
    """Score an alert using only the fixed candles after its alert candle."""
    if not d_min.is_finite() or not a_max.is_finite() or d_min <= ZERO or a_max <= ZERO:
        raise ValueError("d_min and a_max must be positive")
    if lookahead < 1:
        raise ValueError("lookahead must be positive")
    if speed_bonus_candles < 1 or speed_bonus_candles > lookahead:
        raise ValueError("speed_bonus_candles must be within the lookahead window")
    if same_candle_tie not in {"adverse_first", "target_first"}:
        raise ValueError("same_candle_tie must be 'adverse_first' or 'target_first'")
    if (
        not follow_through_per_unit.is_finite()
        or not follow_through_cap.is_finite()
        or follow_through_per_unit < ZERO
        or follow_through_cap < ZERO
    ):
        raise ValueError("follow-through bonus settings must be finite and non-negative")
    if len(future_candles) < lookahead:
        return None, "INSUFFICIENT_LOOKAHEAD"

    window = future_candles[:lookahead]
    max_favorable = ZERO
    target_candle: int | None = None
    adverse_candle: int | None = None

    for offset, candle in enumerate(window, start=1):
        if direction == "BUY":
            favorable = candle.high - entry_price
            target_hit = favorable >= d_min
            adverse_hit = candle.low < entry_price - a_max
        else:
            favorable = entry_price - candle.low
            target_hit = favorable >= d_min
            adverse_hit = candle.high > entry_price + a_max
        max_favorable = max(max_favorable, favorable)
        if target_hit and target_candle is None:
            target_candle = offset
        if adverse_hit and adverse_candle is None:
            adverse_candle = offset

    if target_candle is None:
        return Decimal("-1"), "SCORED"

    adverse_first = adverse_candle is not None and (
        adverse_candle < target_candle
        or (
            adverse_candle == target_candle
            and same_candle_tie == "adverse_first"
        )
    )
    if adverse_first:
        base_score = Decimal("0.5")
    elif target_candle <= speed_bonus_candles:
        base_score = Decimal("1.5")
    else:
        base_score = Decimal("1")

    favorable_units = int(max_favorable // d_min)
    extra_units = max(0, favorable_units - 1)
    bonus = min(
        follow_through_cap,
        Decimal(extra_units) * follow_through_per_unit,
    )
    return base_score + bonus, "SCORED"
