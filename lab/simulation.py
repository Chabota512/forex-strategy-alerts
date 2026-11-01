"""Risk/reward simulations that consume replay alerts without changing the engine."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .engine import Candle, body_engulfs_previous
from .instruments import INSTRUMENT_REGISTRY, simulation_costs

ATR_PERIOD = 14
MAX_EQUITY_POINTS = 160
SIMULATION_COSTS = simulation_costs()
ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
EXIT_MODEL_SWEPT_LEVEL_ATR = "swept_level_atr"
EXIT_MODEL_CANDLE_GEOMETRY = "candle_geometry"
EXIT_MODELS = frozenset({EXIT_MODEL_SWEPT_LEVEL_ATR, EXIT_MODEL_CANDLE_GEOMETRY})
SIZING_MODE_LOTS = "LOTS"
SIZING_MODE_RISK_PERCENT = "RISK_PERCENT"
SIZING_MODES = frozenset({SIZING_MODE_LOTS, SIZING_MODE_RISK_PERCENT})
CANDLE_GEOMETRY_STOP_BODY_WEIGHT = Decimal("1")
CANDLE_GEOMETRY_SWEEP_WICK_MULTIPLIER = Decimal("2")
CANDLE_GEOMETRY_TARGET_RANGE_MULTIPLIER = Decimal("3")
DIRECTION_MODES = frozenset({"normal", "inverted", "adaptive"})
INVERT_FEATURES = (
    "direction",
    "variant",
    "cct_state",
    "cct_alignment",
    "cst_state",
    "cst_alignment",
    "wp_state",
    "mtf_trend_agree",
    "mtf_wp_agree",
    "engulf",
    "dual_tf_alert",
    "strength_score",
)
INVERT_WEIGHT_CAMEL_KEYS = {
    "direction": "direction",
    "variant": "variant",
    "cct_state": "cctState",
    "cct_alignment": "cctAlignment",
    "cst_state": "cstState",
    "cst_alignment": "cstAlignment",
    "wp_state": "wpState",
    "mtf_trend_agree": "mtfTrendAgree",
    "mtf_wp_agree": "mtfWpAgree",
    "engulf": "engulf",
    "dual_tf_alert": "dualTfAlert",
    "strength_score": "strengthScore",
}
DEFAULT_INVERT_WEIGHTS = {
    feature: Decimal("1") / Decimal(len(INVERT_FEATURES))
    for feature in INVERT_FEATURES
}


def _as_decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{name} must be a finite number.")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number.") from exc
    if not parsed.is_finite():
        raise ValueError(f"{name} must be a finite number.")
    return parsed


def normalize_invert_weights(value: Mapping[str, Any] | None) -> dict[str, Decimal]:
    """Validate signed inversion weights and normalize their absolute sum to one."""
    if value is None:
        return dict(DEFAULT_INVERT_WEIGHTS)
    if not isinstance(value, Mapping):
        raise ValueError("invert_weights must be an object.")
    if set(value) == set(INVERT_WEIGHT_CAMEL_KEYS.values()):
        source = {
            feature: value[camel_key]
            for feature, camel_key in INVERT_WEIGHT_CAMEL_KEYS.items()
        }
    elif set(value) == set(INVERT_FEATURES):
        source = value
    else:
        raise ValueError(
            "invert_weights must contain exactly the twelve supported features."
        )
    parsed: dict[str, Decimal] = {}
    for feature in INVERT_FEATURES:
        numeric = _as_decimal(source[feature], f"invert_weights.{feature}")
        if not Decimal("-1") <= numeric <= Decimal("1"):
            raise ValueError(f"invert_weights.{feature} must be between -1 and 1.")
        parsed[feature] = numeric
    magnitude = sum((abs(weight) for weight in parsed.values()), ZERO)
    if magnitude == ZERO:
        raise ValueError("At least one invert_weights value must be non-zero.")
    return {feature: weight / magnitude for feature, weight in parsed.items()}


@dataclass(frozen=True)
class SimulationSettings:
    enabled: bool = True
    starting_balance: Decimal = Decimal("1000")
    risk_percent: Decimal = Decimal("1")
    sizing_mode: str = SIZING_MODE_RISK_PERCENT
    lot_size: Decimal = Decimal("0.01")
    reward_multiple: Decimal = Decimal("2")
    exit_model: str = EXIT_MODEL_SWEPT_LEVEL_ATR
    swing_version: str = "V2"
    stop_body_weight: Decimal = CANDLE_GEOMETRY_STOP_BODY_WEIGHT
    stop_wick_multiplier: Decimal = CANDLE_GEOMETRY_SWEEP_WICK_MULTIPLIER
    target_range_multiplier: Decimal = CANDLE_GEOMETRY_TARGET_RANGE_MULTIPLIER
    direction_mode: str = "normal"
    invert_weights: Mapping[str, Decimal] = field(
        default_factory=lambda: dict(DEFAULT_INVERT_WEIGHTS)
    )
    invert_threshold: Decimal = Decimal("0.5")

    @classmethod
    def from_payload(
        cls,
        value: Mapping[str, Any] | None,
        *,
        symbol: str = "XAUUSD",
    ) -> "SimulationSettings":
        normalized_symbol = symbol.strip().upper()
        instrument = INSTRUMENT_REGISTRY.get(normalized_symbol)
        if instrument is None:
            raise ValueError(f"No simulation cost is configured for {normalized_symbol}.")
        if value is None:
            return cls(lot_size=instrument.default_lot_size)
        if not isinstance(value, Mapping):
            raise ValueError("simulationSettings must be an object.")
        enabled = value.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("Simulation enabled must be a boolean.")
        starting_balance = _as_decimal(
            value.get("startingBalance", "1000"), "Starting balance"
        )
        risk_percent = _as_decimal(value.get("riskPercent", "1"), "Risk percent")
        sizing_mode = value.get("sizingMode", SIZING_MODE_RISK_PERCENT)
        if not isinstance(sizing_mode, str) or sizing_mode not in SIZING_MODES:
            raise ValueError("Sizing mode must be LOTS or RISK_PERCENT.")
        lot_size = _as_decimal(
            value.get("lotSize", instrument.default_lot_size), "Lot size"
        )
        reward_multiple = _as_decimal(
            value.get("rewardMultiple", "2"), "Reward multiple"
        )
        stop_body_weight = _as_decimal(
            value.get("stopBodyWeight", CANDLE_GEOMETRY_STOP_BODY_WEIGHT),
            "Stop body weight",
        )
        stop_wick_multiplier = _as_decimal(
            value.get("stopWickMultiplier", CANDLE_GEOMETRY_SWEEP_WICK_MULTIPLIER),
            "Stop wick multiplier",
        )
        target_range_multiplier = _as_decimal(
            value.get(
                "targetRangeMultiplier",
                CANDLE_GEOMETRY_TARGET_RANGE_MULTIPLIER,
            ),
            "Target range multiplier",
        )
        geometry_weights = value.get("geometryWeights")
        if geometry_weights is not None:
            if not isinstance(geometry_weights, Mapping) or set(geometry_weights) != {
                "bodyWeight",
                "wickMultiplier",
                "rangeMultiplier",
            }:
                raise ValueError(
                    "geometryWeights must contain bodyWeight, wickMultiplier, and rangeMultiplier."
                )
            stop_body_weight = _as_decimal(
                geometry_weights["bodyWeight"], "Geometry body weight"
            )
            stop_wick_multiplier = _as_decimal(
                geometry_weights["wickMultiplier"], "Geometry wick multiplier"
            )
            target_range_multiplier = _as_decimal(
                geometry_weights["rangeMultiplier"], "Geometry range multiplier"
            )
        exit_model = value.get("exitModel", EXIT_MODEL_SWEPT_LEVEL_ATR)
        if not isinstance(exit_model, str) or exit_model not in EXIT_MODELS:
            raise ValueError(
                "Exit model must be swept_level_atr or candle_geometry."
            )
        direction_mode = value.get("directionMode", "normal")
        if not isinstance(direction_mode, str) or direction_mode not in DIRECTION_MODES:
            raise ValueError("Direction mode must be normal, inverted, or adaptive.")
        if normalized_symbol != "XAUUSD" and direction_mode != "normal":
            raise ValueError("Directional inversion modes are available for XAUUSD only.")
        invert_weights_value = value.get("invertWeights")
        if invert_weights_value is None:
            invert_weights_value = value.get("invert_weights")
        try:
            invert_weights = normalize_invert_weights(invert_weights_value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        invert_threshold = _as_decimal(
            value.get("invertThreshold", value.get("invert_threshold", "0.5")),
            "Invert threshold",
        )
        if not ZERO <= invert_threshold <= Decimal("1"):
            raise ValueError("Invert threshold must be between 0 and 1.")
        swing_version = value.get("swingVersion", "V2")
        if isinstance(swing_version, str):
            swing_version = swing_version.upper()
        if swing_version not in {"V1", "V2"}:
            raise ValueError("Swing version must be V1 or V2.")
        if starting_balance <= ZERO or starting_balance > Decimal("1000000000000"):
            raise ValueError("Starting balance must be greater than 0 and at most 1e12.")
        if risk_percent <= ZERO or risk_percent > ONE_HUNDRED:
            raise ValueError("Risk percent must be greater than 0 and at most 100.")
        if lot_size <= ZERO or lot_size > Decimal("1000000"):
            raise ValueError("Lot size must be greater than 0 and at most 1,000,000.")
        if reward_multiple <= ZERO or reward_multiple > Decimal("100"):
            raise ValueError("Reward multiple must be greater than 0 and at most 100.")
        if stop_body_weight <= ZERO:
            raise ValueError("Stop body weight must be greater than 0.")
        if stop_wick_multiplier <= ZERO:
            raise ValueError("Stop wick multiplier must be greater than 0.")
        if target_range_multiplier <= ZERO:
            raise ValueError("Target range multiplier must be greater than 0.")
        return cls(
            enabled=enabled,
            starting_balance=starting_balance,
            risk_percent=risk_percent,
            sizing_mode=sizing_mode,
            lot_size=lot_size,
            reward_multiple=reward_multiple,
            exit_model=exit_model,
            swing_version=swing_version,
            stop_body_weight=stop_body_weight,
            stop_wick_multiplier=stop_wick_multiplier,
            target_range_multiplier=target_range_multiplier,
            direction_mode=direction_mode,
            invert_weights=invert_weights,
            invert_threshold=invert_threshold,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "startingBalance": float(self.starting_balance),
            "riskPercent": float(self.risk_percent),
            "sizingMode": self.sizing_mode,
            "lotSize": float(self.lot_size),
            "rewardMultiple": float(self.reward_multiple),
            "exitModel": self.exit_model,
            "swingVersion": self.swing_version,
            "stopBodyWeight": float(self.stop_body_weight),
            "stopWickMultiplier": float(self.stop_wick_multiplier),
            "targetRangeMultiplier": float(self.target_range_multiplier),
            "directionMode": self.direction_mode,
            "invertWeights": {
                INVERT_WEIGHT_CAMEL_KEYS[feature]: float(self.invert_weights[feature])
                for feature in INVERT_FEATURES
            },
            "invertThreshold": float(self.invert_threshold),
            "geometryWeights": (
                {
                    "bodyWeight": float(self.stop_body_weight),
                    "wickMultiplier": float(self.stop_wick_multiplier),
                    "rangeMultiplier": float(self.target_range_multiplier),
                }
                if self.exit_model == EXIT_MODEL_CANDLE_GEOMETRY
                else None
            ),
        }


def _trend_signal(value: Any) -> Decimal:
    normalized = str(value or "").strip().lower()
    if normalized in {"uptrend", "up-pressure", "buy", "bullish"}:
        return Decimal("1")
    if normalized in {"downtrend", "down-pressure", "sell", "bearish"}:
        return Decimal("-1")
    return ZERO


def _alert_value(alert: Mapping[str, Any], snake: str, camel: str, default: Any = None) -> Any:
    return alert.get(snake, alert.get(camel, default))


def inversion_feature_values(alert: Mapping[str, Any]) -> dict[str, Decimal]:
    """Map causal alert fields to the bounded inputs used by adaptive mode."""
    strength_features = alert.get("strength_features", alert.get("strengthFeatures"))
    if not isinstance(strength_features, Mapping):
        strength_features = {}
    direction = str(alert.get("direction", "")).upper()
    direction_value = Decimal("1") if direction == "BUY" else Decimal("-1") if direction == "SELL" else ZERO
    alert_type = str(_alert_value(alert, "alert_type", "alertType", ""))
    variant_value = (
        Decimal("1")
        if str(alert.get("variant", "B" if alert_type.endswith("-B") else "A")).upper() == "B"
        else ZERO
    )
    cct_state = _trend_signal(_alert_value(alert, "cct", "cctState"))
    cst_state = _trend_signal(
        _alert_value(
            alert,
            "cst",
            "cstState",
            _alert_value(alert, "cst_v1_state", "cstV1State"),
        )
    )

    def alignment(state: Decimal) -> Decimal:
        return state * direction_value if state != ZERO else ZERO

    def binary_feature(value: Any, name: str) -> Decimal:
        if value is True:
            return Decimal("1")
        if value is False or value is None:
            return ZERO
        numeric = _as_decimal(value, name)
        if numeric not in {ZERO, Decimal("1")}:
            raise ValueError(f"{name} must be 0 or 1.")
        return numeric

    cct_alignment = alignment(cct_state)
    if cct_state == ZERO and strength_features.get("cct_alignment") is not None:
        cct_alignment = _as_decimal(
            strength_features["cct_alignment"], "Adaptive CCT alignment"
        )
    cst_alignment = alignment(cst_state)
    if cst_state == ZERO and strength_features.get("cst_alignment") is not None:
        cst_alignment = _as_decimal(
            strength_features["cst_alignment"], "Adaptive CST alignment"
        )

    wp_value = _alert_value(
        alert,
        "wp_state",
        "wpState",
        _alert_value(alert, "wick_pressure", "wickPressure"),
    )
    wp_state = _trend_signal(wp_value)
    if wp_state != ZERO:
        wp_state *= direction_value
    elif strength_features.get("wp_agree") is not None:
        wp_state = _as_decimal(strength_features["wp_agree"], "Adaptive wick pressure")

    mtf_trend = _alert_value(alert, "mtf_trend_agree", "mtfTrendAgree")
    mtf_wp = _alert_value(alert, "mtf_wp_agree", "mtfWpAgree")
    mtf_trend_value = (
        binary_feature(mtf_trend, "Adaptive MTF trend agreement")
        if mtf_trend is not None
        else binary_feature(
            strength_features.get("mtf_trend_agree", "0"),
            "Adaptive MTF trend agreement",
        )
    )
    mtf_wp_value = (
        binary_feature(mtf_wp, "Adaptive MTF wick-pressure agreement")
        if mtf_wp is not None
        else binary_feature(
            strength_features.get("mtf_wp_agree", "0"),
            "Adaptive MTF wick-pressure agreement",
        )
    )
    engulf = _alert_value(alert, "engulfing", "engulf", None)
    if engulf is None:
        engulf = strength_features.get("engulf", False)
    dual_tf = _alert_value(alert, "dual_tf_alert", "dualTfAlert", None)
    if dual_tf is None:
        dual_tf = strength_features.get("dual_tf_alert", False)
    strength_score_value = _alert_value(
        alert, "strength_score", "strengthScore", "0"
    )
    strength_score = _as_decimal(
        "0" if strength_score_value is None else strength_score_value,
        "Adaptive strength score",
    )
    if not ZERO <= strength_score <= Decimal("1"):
        raise ValueError("Adaptive strength score must be between 0 and 1.")
    return {
        "direction": direction_value,
        "variant": variant_value,
        "cct_state": cct_state,
        "cct_alignment": cct_alignment,
        "cst_state": cst_state,
        "cst_alignment": cst_alignment,
        "wp_state": wp_state,
        "mtf_trend_agree": mtf_trend_value,
        "mtf_wp_agree": mtf_wp_value,
        "engulf": binary_feature(engulf, "Adaptive engulf feature"),
        "dual_tf_alert": binary_feature(dual_tf, "Adaptive dual-timeframe feature"),
        "strength_score": strength_score,
    }


def compute_invert_score(
    alert: Mapping[str, Any], weights: Mapping[str, Decimal]
) -> Decimal:
    features = inversion_feature_values(alert)
    denominator = sum((abs(weights[feature]) for feature in INVERT_FEATURES), ZERO)
    if denominator == ZERO:
        raise ValueError("At least one invert_weights value must be non-zero.")
    return sum(
        (weights[feature] * features[feature] for feature in INVERT_FEATURES), ZERO
    ) / denominator


def resolve_simulation_direction(
    alert: Mapping[str, Any], settings: SimulationSettings
) -> tuple[str, str, Decimal | None, bool]:
    """Return source side, executed side, available inversion score, and flag."""
    source_direction = str(alert.get("direction", "")).upper()
    if source_direction not in {"BUY", "SELL"}:
        return source_direction, source_direction, None, False
    score: Decimal | None = None
    if settings.direction_mode == "adaptive":
        score = compute_invert_score(alert, settings.invert_weights)
        inverted = score >= settings.invert_threshold
    elif settings.direction_mode == "inverted":
        score = compute_invert_score(alert, settings.invert_weights)
        inverted = True
    else:
        inverted = False
    applied_direction = (
        "SELL" if source_direction == "BUY" else "BUY"
    ) if inverted else source_direction
    return source_direction, applied_direction, score, inverted


@dataclass(frozen=True)
class SimulationFilters:
    direction: str = "ALL"
    strength: str = "ALL"
    engulfing_only: bool = False
    variant: str = "ALL"
    wick_pressure: str = "ALL"
    mtf_agreement_only: bool = False

    @classmethod
    def from_payload(
        cls, value: Mapping[str, Any] | None
    ) -> "SimulationFilters":
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ValueError("simulationFilters must be an object.")
        direction = value.get("direction", "ALL")
        strength = value.get("strength", "ALL")
        engulfing_only = value.get("engulfingOnly", False)
        variant = value.get("variant", "ALL")
        wick_pressure = value.get("wickPressure", "ALL")
        mtf_agreement_only = value.get("mtfAgreementOnly", False)
        if not isinstance(direction, str) or direction not in {"ALL", "BUY", "SELL"}:
            raise ValueError("Simulation direction filter must be ALL, BUY, or SELL.")
        if not isinstance(strength, str) or strength not in {
            "ALL",
            "STRONG",
            "NEUTRAL",
            "WEAK",
        }:
            raise ValueError(
                "Simulation strength filter must be ALL, STRONG, NEUTRAL, or WEAK."
            )
        if not isinstance(engulfing_only, bool):
            raise ValueError("Simulation engulfingOnly must be a boolean.")
        if not isinstance(variant, str) or variant not in {"ALL", "A", "B"}:
            raise ValueError("Simulation variant filter must be ALL, A, or B.")
        if not isinstance(wick_pressure, str) or wick_pressure not in {
            "ALL",
            "up-pressure",
            "balanced",
            "down-pressure",
        }:
            raise ValueError(
                "Simulation wickPressure filter must be ALL, up-pressure, "
                "balanced, or down-pressure."
            )
        if not isinstance(mtf_agreement_only, bool):
            raise ValueError("Simulation mtfAgreementOnly must be a boolean.")
        return cls(
            direction,
            strength,
            engulfing_only,
            variant,
            wick_pressure,
            mtf_agreement_only,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "strength": self.strength,
            "engulfingOnly": self.engulfing_only,
            "variant": self.variant,
            "wickPressure": self.wick_pressure,
            "mtfAgreementOnly": self.mtf_agreement_only,
        }


@dataclass
class _Trade:
    trade_id: str
    alert_id: str
    symbol: str
    source_direction: str
    direction: str
    alert_type: str
    entry_index: int
    entry_time: str
    entry_price: Decimal
    position_size: Decimal
    lot_size: Decimal
    sizing_mode: str
    contract_size: Decimal
    pip_size: Decimal
    pnl_currency: str
    risk_amount: Decimal
    stop_price: Decimal
    take_profit_price: Decimal
    exit_model: str
    sl_inside_sweep_tip: bool
    direction_mode: str
    invert_score: Decimal | None
    is_inverted: bool
    geometry_weights: dict[str, Decimal] | None = None
    exit_index: int | None = None
    exit_time: str | None = None
    exit_price: Decimal | None = None
    outcome: str = "OPEN"
    exit_reason: str | None = None
    gross_pnl: Decimal = ZERO
    cost: Decimal = ZERO
    net_pnl: Decimal = ZERO
    realized_r: Decimal = ZERO
    net_r: Decimal = ZERO
    price_move: Decimal = ZERO
    pip_pnl: Decimal = ZERO

    def to_dict(self, *, as_open: bool = False) -> dict[str, Any]:
        is_open = as_open or self.outcome == "OPEN"
        return {
            "tradeId": self.trade_id,
            "alertId": self.alert_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "sourceDirection": self.source_direction,
            "appliedDirection": self.direction,
            "directionMode": self.direction_mode,
            "invertScore": (
                None if self.invert_score is None else float(self.invert_score)
            ),
            "inverted": self.is_inverted,
            "alertType": self.alert_type,
            "entryIndex": self.entry_index,
            "entryTime": self.entry_time,
            "entryPrice": float(self.entry_price),
            "positionSize": float(self.position_size),
            "lotSize": float(self.lot_size),
            "sizingMode": self.sizing_mode,
            "contractSize": float(self.contract_size),
            "pipSize": float(self.pip_size),
            "pnlCurrency": self.pnl_currency,
            "priceMove": 0.0 if is_open else float(self.price_move),
            "pipPnl": 0.0 if is_open else float(self.pip_pnl),
            "riskAmount": float(self.risk_amount),
            "stopPrice": float(self.stop_price),
            "takeProfitPrice": float(self.take_profit_price),
            "exitModel": self.exit_model,
            "geometryWeights": (
                {
                    key: float(value)
                    for key, value in self.geometry_weights.items()
                }
                if self.geometry_weights is not None
                else None
            ),
            "slInsideSweepTip": self.sl_inside_sweep_tip,
            "exitIndex": None if is_open else self.exit_index,
            "exitTime": None if is_open else self.exit_time,
            "exitPrice": None if is_open or self.exit_price is None else float(self.exit_price),
            "outcome": "OPEN" if is_open else self.outcome,
            "exitReason": None if is_open else self.exit_reason,
            "grossPnl": 0.0 if is_open else float(self.gross_pnl),
            "cost": 0.0 if is_open else float(self.cost),
            "netPnl": 0.0 if is_open else float(self.net_pnl),
            "realizedR": 0.0 if is_open else float(self.realized_r),
            "netR": 0.0 if is_open else float(self.net_r),
        }


class SimulationSession:
    """Incremental account simulation driven by closed candles and replay alerts."""

    def __init__(
        self,
        records: list[dict[str, Any]],
        *,
        symbol: str = "XAUUSD",
        settings: Mapping[str, Any] | None = None,
    ):
        symbol = symbol.strip().upper()
        if symbol not in INSTRUMENT_REGISTRY:
            raise ValueError(f"No simulation cost is configured for {symbol}.")
        self.records = records
        self.symbol = symbol
        self.settings = SimulationSettings.from_payload(settings, symbol=symbol)
        self.balance = self.settings.starting_balance
        self._true_ranges: deque[Decimal] = deque(maxlen=ATR_PERIOD)
        self._atr_values: list[Decimal] = []
        self._previous_close: Decimal | None = None
        self._pending_alerts: list[tuple[int, dict[str, Any]]] = []
        self.trades: list[_Trade] = []
        self._mirror_trades: list[_Trade] = []
        self.opened_events: list[dict[str, Any]] = []
        self.resolved_events: list[dict[str, Any]] = []
        self.equity_points: list[tuple[int, str, Decimal]] = []
        self._processed_through = -1

    def queue_alert(
        self,
        candle_index: int,
        alert: dict[str, Any],
        filters: SimulationFilters | Mapping[str, Any] | None = None,
    ) -> bool:
        """Queue a matching alert; it can only enter at the following candle open."""
        if not self.settings.enabled:
            return False
        if candle_index < 0 or candle_index >= len(self.records):
            raise ValueError("Alert index is outside the simulation candle window.")
        active_filters = (
            filters
            if isinstance(filters, SimulationFilters)
            else SimulationFilters.from_payload(filters)
        )
        if not self._matches_filters(alert, active_filters):
            return False
        self._pending_alerts.append((candle_index, alert))
        return True

    def atr_at(self, index: int) -> Decimal | None:
        """Return the simulator's already-computed ATR value for a candle."""
        if index < 0 or index >= len(self._atr_values):
            return None
        return self._atr_values[index]

    def process_candle(self, index: int) -> None:
        """Update ATR, open prior-candle alerts at this open, then resolve intrabar."""
        if index != self._processed_through + 1:
            raise ValueError("Simulation candles must be processed once in order.")
        record = self.records[index]
        high = _as_decimal(record["high"], "Candle high")
        low = _as_decimal(record["low"], "Candle low")
        close = _as_decimal(record["close"], "Candle close")
        if self._previous_close is None:
            true_range = high - low
        else:
            true_range = max(
                high - low,
                abs(high - self._previous_close),
                abs(low - self._previous_close),
            )
        self._true_ranges.append(true_range)
        atr = sum(self._true_ranges, ZERO) / Decimal(len(self._true_ranges))
        self._atr_values.append(atr)
        self._previous_close = close

        if self.settings.enabled:
            eligible = [
                item for item in self._pending_alerts if item[0] < index
            ]
            self._pending_alerts = [
                item for item in self._pending_alerts if item[0] >= index
            ]
            for alert_index, alert in eligible:
                self._open_trade(alert_index, alert, record, index)
            self._resolve_open_trades(record, index)

        self.equity_points.append((index, str(record["time"]), self.balance))
        self._processed_through = index

    def snapshot(self, index: int) -> dict[str, Any]:
        """Return a bounded equity strip and statistics as of a replay cursor."""
        if not self.equity_points:
            equity_points: list[tuple[int, str, Decimal]] = []
            balance = self.settings.starting_balance
            bounded_index = max(0, index)
        else:
            bounded_index = min(max(index, 0), self._processed_through)
            equity_points = [
                point for point in self.equity_points if point[0] <= bounded_index
            ]
            balance = equity_points[-1][2] if equity_points else self.settings.starting_balance
        bounded_index = min(max(index, 0), self._processed_through)
        closed = [
            trade
            for trade in self.trades
            if trade.exit_index is not None and trade.exit_index <= bounded_index
        ]
        open_count = sum(
            1
            for trade in self.trades
            if trade.entry_index <= bounded_index
            and (trade.exit_index is None or trade.exit_index > bounded_index)
        )
        return {
            "index": bounded_index,
            "enabled": self.settings.enabled,
            "balance": float(balance),
            "openTradeCount": open_count,
            "closedTradeCount": len(closed),
            "settings": self.settings.to_dict(),
            "stats": self._stats(closed, equity_points),
            "equityCurve": self._sample_equity(equity_points),
        }

    def trades_at(self, index: int) -> list[dict[str, Any]]:
        """Return all trades visible at an index, masking outcomes from the future."""
        return [
            trade.to_dict(as_open=trade.exit_index is not None and trade.exit_index > index)
            for trade in self.trades
            if trade.entry_index <= index
        ]

    def mirror_trades_at(self, index: int) -> list[dict[str, Any]]:
        """Return source-signal fade trades used only for counterfactual analysis."""
        return [
            trade.to_dict(as_open=trade.exit_index is not None and trade.exit_index > index)
            for trade in self._mirror_trades
            if trade.entry_index <= index
        ]

    def event_counts(self) -> tuple[int, int]:
        return len(self.opened_events), len(self.resolved_events)

    def event_deltas(
        self, opened_from: int, resolved_from: int
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return (
            self.opened_events[opened_from:],
            self.resolved_events[resolved_from:],
        )

    def _matches_filters(
        self, alert: Mapping[str, Any], filters: SimulationFilters
    ) -> bool:
        direction = str(alert.get("direction", ""))
        strength = str(alert.get("strength_label", alert.get("strengthLabel", "")))
        alert_type = str(alert.get("alert_type", alert.get("alertType", "")))
        variant = "B" if alert_type.endswith("-B") else "A"
        wick_pressure = str(
            alert.get("wick_pressure", alert.get("wickPressure", ""))
        )
        if filters.direction != "ALL" and direction != filters.direction:
            return False
        if filters.strength != "ALL" and strength != filters.strength:
            return False
        if filters.variant != "ALL" and variant != filters.variant:
            return False
        if (
            filters.wick_pressure != "ALL"
            and wick_pressure != filters.wick_pressure
        ):
            return False
        if filters.mtf_agreement_only and alert.get(
            "mtf_trend_agree", alert.get("mtfTrendAgree")
        ) is not True:
            return False
        if filters.engulfing_only:
            if isinstance(alert.get("engulfing"), bool):
                return bool(alert["engulfing"])
            previous_record = alert.get("previous_candle", alert.get("previousCandle"))
            current_record = alert.get("candle")
            if not isinstance(previous_record, Mapping) or not isinstance(
                current_record, Mapping
            ):
                return False
            previous = _record_to_candle(previous_record)
            current = _record_to_candle(current_record)
            return body_engulfs_previous(previous, current, direction)  # type: ignore[arg-type]
        return True

    def _open_trade(
        self,
        alert_index: int,
        alert: Mapping[str, Any],
        entry_record: Mapping[str, Any],
        entry_index: int,
    ) -> None:
        source_direction, direction, invert_score, is_inverted = (
            resolve_simulation_direction(alert, self.settings)
        )
        if source_direction not in {"BUY", "SELL"}:
            return
        alert_candle = alert.get("candle")
        if not isinstance(alert_candle, Mapping):
            return
        entry = _as_decimal(entry_record["open"], "Entry price")
        alert_open = _as_decimal(alert_candle["open"], "Alert candle open")
        alert_high = _as_decimal(alert_candle["high"], "Alert candle high")
        alert_low = _as_decimal(alert_candle["low"], "Alert candle low")
        alert_close = _as_decimal(alert_candle["close"], "Alert candle close")
        normal_sl_inside_sweep_tip = False
        if self.settings.exit_model == EXIT_MODEL_SWEPT_LEVEL_ATR:
            atr = self._atr_values[alert_index]
            swept_level = alert_low if source_direction == "BUY" else alert_high
            directional_distance = (
                entry - swept_level
                if source_direction == "BUY"
                else swept_level - entry
            )
            normal_stop_distance = max(directional_distance, atr)
            normal_stop_price = (
                entry - normal_stop_distance
                if source_direction == "BUY"
                else entry + normal_stop_distance
            )
            take_profit_distance = normal_stop_distance * self.settings.reward_multiple
            normal_take_profit_price = (
                entry + take_profit_distance
                if source_direction == "BUY"
                else entry - take_profit_distance
            )
        else:
            body = abs(alert_close - alert_open)
            candle_range = alert_high - alert_low
            if source_direction == "BUY":
                sweeping_wick = min(alert_open, alert_close) - alert_low
                normal_stop_distance = (
                    self.settings.stop_body_weight * body
                    + self.settings.stop_wick_multiplier * sweeping_wick
                )
                normal_stop_price = entry - normal_stop_distance
                normal_take_profit_price = (
                    entry + self.settings.target_range_multiplier * candle_range
                )
                normal_sl_inside_sweep_tip = normal_stop_price > alert_low
            else:
                sweeping_wick = alert_high - max(alert_open, alert_close)
                normal_stop_distance = (
                    self.settings.stop_body_weight * body
                    + self.settings.stop_wick_multiplier * sweeping_wick
                )
                normal_stop_price = entry + normal_stop_distance
                normal_take_profit_price = (
                    entry - self.settings.target_range_multiplier * candle_range
                )
                normal_sl_inside_sweep_tip = normal_stop_price < alert_high
        if normal_stop_distance <= ZERO:
            return

        actual_stop_price = (
            normal_take_profit_price if is_inverted else normal_stop_price
        )
        actual_take_profit_price = (
            normal_stop_price if is_inverted else normal_take_profit_price
        )
        trade = self._make_trade(
            alert,
            entry_record,
            entry_index,
            entry,
            source_direction=source_direction,
            direction=direction,
            stop_price=actual_stop_price,
            take_profit_price=actual_take_profit_price,
            sl_inside_sweep_tip=(
                normal_sl_inside_sweep_tip if not is_inverted else False
            ),
            invert_score=invert_score,
            is_inverted=is_inverted,
        )
        if trade is None:
            return
        self.trades.append(trade)
        self.opened_events.append(trade.to_dict())

        mirror_direction = "SELL" if source_direction == "BUY" else "BUY"
        mirror_trade = self._make_trade(
            alert,
            entry_record,
            entry_index,
            entry,
            source_direction=source_direction,
            direction=mirror_direction,
            stop_price=normal_take_profit_price,
            take_profit_price=normal_stop_price,
            sl_inside_sweep_tip=False,
            invert_score=invert_score,
            is_inverted=True,
            suffix="fade",
        )
        if mirror_trade is not None:
            self._mirror_trades.append(mirror_trade)

    def _make_trade(
        self,
        alert: Mapping[str, Any],
        entry_record: Mapping[str, Any],
        entry_index: int,
        entry: Decimal,
        *,
        source_direction: str,
        direction: str,
        stop_price: Decimal,
        take_profit_price: Decimal,
        sl_inside_sweep_tip: bool,
        invert_score: Decimal | None,
        is_inverted: bool,
        suffix: str = "sim",
    ) -> _Trade | None:
        stop_distance = abs(entry - stop_price)
        if stop_distance <= ZERO:
            return None
        symbol = str(alert.get("symbol", self.symbol)).strip().upper()
        if symbol not in INSTRUMENT_REGISTRY:
            raise ValueError(f"No simulation cost is configured for {symbol}.")
        instrument = INSTRUMENT_REGISTRY[symbol]
        if self.settings.sizing_mode == SIZING_MODE_LOTS:
            lot_size = self.settings.lot_size
            position_size = lot_size * instrument.contract_size
            risk_amount = position_size * stop_distance
        else:
            risk_amount = self.balance * self.settings.risk_percent / ONE_HUNDRED
            position_size = risk_amount / stop_distance
            lot_size = position_size / instrument.contract_size
        trade = _Trade(
            trade_id=f"{alert.get('alert_id', alert.get('alertId', 'alert'))}-{suffix}",
            alert_id=str(alert.get("alert_id", alert.get("alertId", ""))),
            symbol=symbol,
            source_direction=source_direction,
            direction=direction,
            alert_type=str(
                alert.get("alert_type", alert.get("alertType", f"{source_direction}-A"))
            ),
            entry_index=entry_index,
            entry_time=str(entry_record["time"]),
            entry_price=entry,
            position_size=position_size,
            lot_size=lot_size,
            sizing_mode=self.settings.sizing_mode,
            contract_size=instrument.contract_size,
            pip_size=instrument.pip_size,
            pnl_currency=instrument.quote_currency,
            risk_amount=risk_amount,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            exit_model=self.settings.exit_model,
            sl_inside_sweep_tip=sl_inside_sweep_tip,
            direction_mode=self.settings.direction_mode,
            invert_score=invert_score,
            is_inverted=is_inverted,
            geometry_weights=(
                {
                    "bodyWeight": self.settings.stop_body_weight,
                    "wickMultiplier": self.settings.stop_wick_multiplier,
                    "rangeMultiplier": self.settings.target_range_multiplier,
                }
                if self.settings.exit_model == EXIT_MODEL_CANDLE_GEOMETRY
                else None
            ),
        )
        return trade

    def _resolve_open_trades(
        self, record: Mapping[str, Any], index: int
    ) -> None:
        high = _as_decimal(record["high"], "Candle high")
        low = _as_decimal(record["low"], "Candle low")
        for trades, update_balance, record_events in (
            (self.trades, True, True),
            (self._mirror_trades, False, False),
        ):
            for trade in trades:
                if trade.outcome != "OPEN" or trade.entry_index > index:
                    continue
                stop_hit = (
                    low <= trade.stop_price
                    if trade.direction == "BUY"
                    else high >= trade.stop_price
                )
                target_hit = (
                    high >= trade.take_profit_price
                    if trade.direction == "BUY"
                    else low <= trade.take_profit_price
                )
                if stop_hit:
                    self._close_trade(
                        trade,
                        record,
                        index,
                        trade.stop_price,
                        "SL",
                        update_balance=update_balance,
                        record_event=record_events,
                    )
                elif target_hit:
                    self._close_trade(
                        trade,
                        record,
                        index,
                        trade.take_profit_price,
                        "TP",
                        update_balance=update_balance,
                        record_event=record_events,
                    )

    def _close_trade(
        self,
        trade: _Trade,
        record: Mapping[str, Any],
        index: int,
        exit_price: Decimal,
        exit_reason: str,
        *,
        update_balance: bool = True,
        record_event: bool = True,
    ) -> None:
        signed_move = (
            exit_price - trade.entry_price
            if trade.direction == "BUY"
            else trade.entry_price - exit_price
        )
        trade.exit_index = index
        trade.exit_time = str(record["time"])
        trade.exit_price = exit_price
        trade.exit_reason = exit_reason
        trade.price_move = signed_move
        trade.pip_pnl = signed_move / INSTRUMENT_REGISTRY[trade.symbol].pip_size
        trade.gross_pnl = signed_move * trade.position_size
        trade.cost = INSTRUMENT_REGISTRY[trade.symbol].simulation_cost * trade.position_size
        trade.net_pnl = trade.gross_pnl - trade.cost
        trade.realized_r = trade.gross_pnl / trade.risk_amount
        trade.net_r = trade.net_pnl / trade.risk_amount
        trade.outcome = "WIN" if trade.net_pnl > ZERO else "LOSS"
        if update_balance:
            self.balance += trade.net_pnl
        if record_event:
            self.resolved_events.append(trade.to_dict())

    def _stats(
        self,
        closed: list[_Trade],
        equity_points: list[tuple[int, str, Decimal]],
    ) -> dict[str, Any]:
        wins = [trade for trade in closed if trade.net_pnl > ZERO]
        losses = [trade for trade in closed if trade.net_pnl <= ZERO]
        gross_gains = sum((trade.net_pnl for trade in wins), ZERO)
        gross_losses = abs(
            sum((trade.net_pnl for trade in losses if trade.net_pnl < ZERO), ZERO)
        )
        peak = self.settings.starting_balance
        max_drawdown = ZERO
        max_drawdown_pct = ZERO
        for _index, _time, equity in equity_points:
            peak = max(peak, equity)
            drawdown = peak - equity
            max_drawdown = max(max_drawdown, drawdown)
            if peak > ZERO:
                max_drawdown_pct = max(
                    max_drawdown_pct, drawdown / peak * ONE_HUNDRED
                )
        count = len(closed)
        return {
            "closedTradeCount": count,
            "wins": len(wins),
            "losses": len(losses),
            "winRatePct": float(Decimal(len(wins)) / count * ONE_HUNDRED)
            if count
            else 0.0,
            "avgR": float(
                sum((trade.realized_r for trade in closed), ZERO) / count
            )
            if count
            else 0.0,
            "expectancyAfterCosts": float(
                sum((trade.net_r for trade in closed), ZERO) / count
            )
            if count
            else 0.0,
            "expectancyMoneyAfterCosts": float(
                sum((trade.net_pnl for trade in closed), ZERO) / count
            )
            if count
            else 0.0,
            "maxDrawdown": float(max_drawdown),
            "maxDrawdownPct": float(max_drawdown_pct),
            "profitFactor": float(gross_gains / gross_losses)
            if gross_losses > ZERO
            else None,
        }

    @staticmethod
    def _sample_equity(
        points: list[tuple[int, str, Decimal]]
    ) -> list[dict[str, Any]]:
        if len(points) <= MAX_EQUITY_POINTS:
            selected = points
        else:
            last = len(points) - 1
            selected = [
                points[(position * last) // (MAX_EQUITY_POINTS - 1)]
                for position in range(MAX_EQUITY_POINTS)
            ]
        return [
            {"index": index, "time": time_value, "equity": float(equity)}
            for index, time_value, equity in selected
        ]


def simulate_batch(
    records: list[dict[str, Any]],
    alerts_by_index: Mapping[int, list[dict[str, Any]]],
    *,
    symbol: str = "XAUUSD",
    settings: Mapping[str, Any] | None = None,
    filters: SimulationFilters | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the same candle/alert transitions in a batch for parity comparisons."""
    session = SimulationSession(records, symbol=symbol, settings=settings)
    active_filters = (
        filters
        if isinstance(filters, SimulationFilters)
        else SimulationFilters.from_payload(filters)
    )
    for index in range(len(records)):
        session.process_candle(index)
        for alert in alerts_by_index.get(index, []):
            session.queue_alert(index, alert, active_filters)
    final_index = max(0, len(records) - 1)
    atr14_by_alert_id = {
        str(alert.get("alert_id", alert.get("alertId", ""))): session.atr_at(index)
        for index, alerts in alerts_by_index.items()
        for alert in alerts
        if alert.get("alert_id", alert.get("alertId"))
    }
    return {
        "simulation": session.snapshot(final_index),
        "trades": session.trades_at(final_index),
        "mirrorTrades": session.mirror_trades_at(final_index),
        "atr14_by_alert_id": atr14_by_alert_id,
    }


def _record_to_candle(record: Mapping[str, Any]) -> Candle:
    return Candle.from_values(
        str(record["time"]),
        _as_decimal(record["open"], "Candle open"),
        _as_decimal(record["high"], "Candle high"),
        _as_decimal(record["low"], "Candle low"),
        _as_decimal(record["close"], "Candle close"),
    )
