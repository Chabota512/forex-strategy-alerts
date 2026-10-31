"""Pure, deterministic candle rules for Strategy Lab Phase 1."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Mapping

Direction = Literal["BUY", "SELL"]
VariantBAlertType = Literal["BUY-B", "SELL-B"]
Trend = Literal["uptrend", "downtrend", "ranging"]
CSTState = Literal["uptrend", "downtrend", "transition", "undefined"]
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
    "strong_min": Decimal("0.67"),
    "neutral_min": Decimal("0.33"),
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
    """Evaluate the opt-in same-color sweep without changing the v1.5 trigger."""
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


def classify_cct(
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


def _breaks_level(candle: Candle, level: Decimal, direction: str, basis: str) -> bool:
    if basis == "close":
        return candle.close > level if direction == "up" else candle.close < level
    if basis == "wick":
        return candle.high > level if direction == "up" else candle.low < level
    raise ValueError("break_basis must be 'close' or 'wick'")


def compute_cst(
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
    """Validate bounded weights and normalize their sum to exactly one."""
    source = DEFAULT_STRENGTH_WEIGHTS if weights is None else weights
    if not isinstance(source, Mapping) or set(source) != set(STRENGTH_FEATURES):
        raise ValueError("strength_weights must contain exactly the seven supported features.")
    parsed: dict[str, Decimal] = {}
    for feature in STRENGTH_FEATURES:
        value = source[feature]
        if isinstance(value, bool):
            raise ValueError(f"strength_weights.{feature} must be a number from 0 to 1.")
        try:
            weight = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(
                f"strength_weights.{feature} must be a number from 0 to 1."
            ) from exc
        if not weight.is_finite() or not ZERO <= weight <= Decimal("1"):
            raise ValueError(f"strength_weights.{feature} must be a number from 0 to 1.")
        parsed[feature] = weight
    total = sum(parsed.values(), ZERO)
    if total <= ZERO:
        raise ValueError("strength_weights must have a positive total.")
    normalized = {feature: parsed[feature] / total for feature in STRENGTH_FEATURES}
    # Absorb Decimal division rounding in the last entry so consumers receive
    # a stable, sum-normalized map.
    preceding = sum(
        (normalized[feature] for feature in STRENGTH_FEATURES[:-1]),
        ZERO,
    )
    normalized[STRENGTH_FEATURES[-1]] = Decimal("1") - preceding
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
    """
    if not isinstance(features, Mapping) or set(features) != set(STRENGTH_FEATURES):
        raise ValueError("Strength features do not match the supported feature set.")
    normalized_weights = normalize_strength_weights(weights)
    normalized_thresholds = normalize_strength_thresholds(thresholds)
    score = ZERO
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
        score += normalized_weights[feature] * value
    score = score.quantize(Decimal("0.000001"))
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
