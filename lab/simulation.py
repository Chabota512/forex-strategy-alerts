"""Risk/reward simulations that consume replay alerts without changing the engine."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
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


@dataclass(frozen=True)
class SimulationSettings:
    enabled: bool = True
    starting_balance: Decimal = Decimal("10000")
    risk_percent: Decimal = Decimal("1")
    sizing_mode: str = SIZING_MODE_RISK_PERCENT
    lot_size: Decimal = Decimal("0.01")
    reward_multiple: Decimal = Decimal("2")
    exit_model: str = EXIT_MODEL_SWEPT_LEVEL_ATR
    stop_body_weight: Decimal = CANDLE_GEOMETRY_STOP_BODY_WEIGHT
    stop_wick_multiplier: Decimal = CANDLE_GEOMETRY_SWEEP_WICK_MULTIPLIER
    target_range_multiplier: Decimal = CANDLE_GEOMETRY_TARGET_RANGE_MULTIPLIER

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
            value.get("startingBalance", "10000"), "Starting balance"
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
        exit_model = value.get("exitModel", EXIT_MODEL_SWEPT_LEVEL_ATR)
        if not isinstance(exit_model, str) or exit_model not in EXIT_MODELS:
            raise ValueError(
                "Exit model must be swept_level_atr or candle_geometry."
            )
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
            stop_body_weight=stop_body_weight,
            stop_wick_multiplier=stop_wick_multiplier,
            target_range_multiplier=target_range_multiplier,
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
            "stopBodyWeight": float(self.stop_body_weight),
            "stopWickMultiplier": float(self.stop_wick_multiplier),
            "targetRangeMultiplier": float(self.target_range_multiplier),
        }


@dataclass(frozen=True)
class SimulationFilters:
    direction: str = "ALL"
    strength: str = "ALL"
    engulfing_only: bool = False

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
        return cls(direction, strength, engulfing_only)

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "strength": self.strength,
            "engulfingOnly": self.engulfing_only,
        }


@dataclass
class _Trade:
    trade_id: str
    alert_id: str
    symbol: str
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
        if filters.direction != "ALL" and direction != filters.direction:
            return False
        if filters.strength != "ALL" and strength != filters.strength:
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
        direction = str(alert.get("direction", ""))
        if direction not in {"BUY", "SELL"}:
            return
        alert_candle = alert.get("candle")
        if not isinstance(alert_candle, Mapping):
            return
        entry = _as_decimal(entry_record["open"], "Entry price")
        alert_open = _as_decimal(alert_candle["open"], "Alert candle open")
        alert_high = _as_decimal(alert_candle["high"], "Alert candle high")
        alert_low = _as_decimal(alert_candle["low"], "Alert candle low")
        alert_close = _as_decimal(alert_candle["close"], "Alert candle close")
        sl_inside_sweep_tip = False
        if self.settings.exit_model == EXIT_MODEL_SWEPT_LEVEL_ATR:
            atr = self._atr_values[alert_index]
            swept_level = alert_low if direction == "BUY" else alert_high
            directional_distance = (
                entry - swept_level if direction == "BUY" else swept_level - entry
            )
            stop_distance = max(directional_distance, atr)
            stop_price = (
                entry - stop_distance if direction == "BUY" else entry + stop_distance
            )
            take_profit_distance = stop_distance * self.settings.reward_multiple
            take_profit_price = (
                entry + take_profit_distance
                if direction == "BUY"
                else entry - take_profit_distance
            )
        else:
            body = abs(alert_close - alert_open)
            candle_range = alert_high - alert_low
            if direction == "BUY":
                sweeping_wick = min(alert_open, alert_close) - alert_low
                stop_distance = (
                    self.settings.stop_body_weight * body
                    + self.settings.stop_wick_multiplier * sweeping_wick
                )
                stop_price = entry - stop_distance
                take_profit_price = (
                    entry + self.settings.target_range_multiplier * candle_range
                )
                sl_inside_sweep_tip = stop_price > alert_low
            else:
                sweeping_wick = alert_high - max(alert_open, alert_close)
                stop_distance = (
                    self.settings.stop_body_weight * body
                    + self.settings.stop_wick_multiplier * sweeping_wick
                )
                stop_price = entry + stop_distance
                take_profit_price = (
                    entry - self.settings.target_range_multiplier * candle_range
                )
                sl_inside_sweep_tip = stop_price < alert_high
        if stop_distance <= ZERO:
            return

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
            trade_id=f"{alert.get('alert_id', alert.get('alertId', 'alert'))}-sim",
            alert_id=str(alert.get("alert_id", alert.get("alertId", ""))),
            symbol=symbol,
            direction=direction,
            alert_type=str(alert.get("alert_type", alert.get("alertType", f"{direction}-A"))),
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
        )
        self.trades.append(trade)
        self.opened_events.append(trade.to_dict())

    def _resolve_open_trades(
        self, record: Mapping[str, Any], index: int
    ) -> None:
        high = _as_decimal(record["high"], "Candle high")
        low = _as_decimal(record["low"], "Candle low")
        for trade in self.trades:
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
                self._close_trade(trade, record, index, trade.stop_price, "SL")
            elif target_hit:
                self._close_trade(trade, record, index, trade.take_profit_price, "TP")

    def _close_trade(
        self,
        trade: _Trade,
        record: Mapping[str, Any],
        index: int,
        exit_price: Decimal,
        exit_reason: str,
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
        self.balance += trade.net_pnl
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
