"""Capital assumptions carried alongside informational research metrics."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

DEFAULT_CAPITAL_CONTEXT: dict[str, Any] = {
    "balance_usd": 1000.0,
    "sizing": "LOTS",
    "lots": {"XAUUSD": 0.01},
}


def capital_context_from_settings(
    settings: Mapping[str, Any] | None,
    *,
    symbol: str = "XAUUSD",
) -> dict[str, Any]:
    """Normalize validated simulation settings to the saved capital context."""
    if settings is None:
        return {
            **DEFAULT_CAPITAL_CONTEXT,
            "lots": dict(DEFAULT_CAPITAL_CONTEXT["lots"]),
        }
    if not isinstance(settings, Mapping):
        raise ValueError("Simulation settings for capital context must be an object.")

    balance_value = settings.get(
        "startingBalance", settings.get("starting_balance", 1000)
    )
    lot_value = settings.get("lotSize", settings.get("lot_size", 0.01))
    sizing_value = settings.get(
        "sizingMode", settings.get("sizing_mode", "LOTS")
    )
    try:
        balance = Decimal(str(balance_value))
        lot_size = Decimal(str(lot_value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Simulation capital context contains an invalid number.") from exc
    if not balance.is_finite() or balance <= 0:
        raise ValueError("Capital context balance must be a positive finite number.")
    if not lot_size.is_finite() or lot_size <= 0:
        raise ValueError("Capital context lot size must be a positive finite number.")
    if not isinstance(sizing_value, str) or sizing_value.upper() not in {
        "LOTS",
        "RISK_PERCENT",
    }:
        raise ValueError("Capital context sizing must be LOTS or RISK_PERCENT.")

    normalized_symbol = symbol.strip().upper()
    return {
        "balance_usd": float(balance),
        "sizing": sizing_value.upper(),
        "lots": {normalized_symbol: float(lot_size)},
    }


def normalize_capital_context(
    value: Any,
    *,
    symbol: str = "XAUUSD",
) -> dict[str, Any]:
    """Return a JSON-safe context, using the documented gold default if absent."""
    if not isinstance(value, Mapping):
        return capital_context_from_settings(None, symbol=symbol)

    balance = value.get("balance_usd", value.get("balanceUsd"))
    sizing = value.get("sizing")
    lots = value.get("lots")
    if not isinstance(balance, (int, float)) or isinstance(balance, bool):
        return capital_context_from_settings(None, symbol=symbol)
    if not isinstance(sizing, str) or sizing.upper() not in {"LOTS", "RISK_PERCENT"}:
        return capital_context_from_settings(None, symbol=symbol)
    if not isinstance(lots, Mapping):
        return capital_context_from_settings(None, symbol=symbol)
    normalized_lots: dict[str, float] = {}
    for lot_symbol, lot_value in lots.items():
        if not isinstance(lot_symbol, str):
            continue
        try:
            lot_size = Decimal(str(lot_value))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if lot_size.is_finite() and lot_size > 0:
            normalized_lots[lot_symbol.strip().upper()] = float(lot_size)
    if not normalized_lots:
        return capital_context_from_settings(None, symbol=symbol)
    return {
        "balance_usd": float(balance),
        "sizing": sizing.upper(),
        "lots": normalized_lots,
    }
