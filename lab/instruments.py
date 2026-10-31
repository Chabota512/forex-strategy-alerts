"""Owner-adjustable instrument registry shared by data and simulation code."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Instrument:
    symbol: str
    simulation_cost: Decimal
    contract_size: Decimal
    default_lot_size: Decimal
    pip_size: Decimal
    quote_currency: str


# Adjust these named constants when the owner changes an instrument assumption.
XAUUSD_SIMULATION_COST = Decimal("0.35")
GBPUSD_SIMULATION_COST = Decimal("0.0001")
USDJPY_SIMULATION_COST = Decimal("0.0001")
GBPJPY_SIMULATION_COST = Decimal("0.015")
AUDJPY_SIMULATION_COST = Decimal("0.010")

XAUUSD_CONTRACT_SIZE = Decimal("100")
GBPUSD_CONTRACT_SIZE = Decimal("100000")
USDJPY_CONTRACT_SIZE = Decimal("100000")
GBPJPY_CONTRACT_SIZE = Decimal("100000")
AUDJPY_CONTRACT_SIZE = Decimal("100000")

XAUUSD_DEFAULT_LOT_SIZE = Decimal("0.01")
GBPUSD_DEFAULT_LOT_SIZE = Decimal("0.10")
USDJPY_DEFAULT_LOT_SIZE = Decimal("0.10")
GBPJPY_DEFAULT_LOT_SIZE = Decimal("0.10")
AUDJPY_DEFAULT_LOT_SIZE = Decimal("0.10")

XAUUSD_PIP_SIZE = Decimal("0.10")
GBPUSD_PIP_SIZE = Decimal("0.0001")
USDJPY_PIP_SIZE = Decimal("0.01")
GBPJPY_PIP_SIZE = Decimal("0.01")
AUDJPY_PIP_SIZE = Decimal("0.01")

INSTRUMENT_REGISTRY: dict[str, Instrument] = {
    instrument.symbol: instrument
    for instrument in (
        Instrument(
            "XAUUSD", XAUUSD_SIMULATION_COST, XAUUSD_CONTRACT_SIZE,
            XAUUSD_DEFAULT_LOT_SIZE, XAUUSD_PIP_SIZE, "USD",
        ),
        Instrument(
            "GBPUSD", GBPUSD_SIMULATION_COST, GBPUSD_CONTRACT_SIZE,
            GBPUSD_DEFAULT_LOT_SIZE, GBPUSD_PIP_SIZE, "USD",
        ),
        Instrument(
            "USDJPY", USDJPY_SIMULATION_COST, USDJPY_CONTRACT_SIZE,
            USDJPY_DEFAULT_LOT_SIZE, USDJPY_PIP_SIZE, "JPY",
        ),
        Instrument(
            "GBPJPY", GBPJPY_SIMULATION_COST, GBPJPY_CONTRACT_SIZE,
            GBPJPY_DEFAULT_LOT_SIZE, GBPJPY_PIP_SIZE, "JPY",
        ),
        Instrument(
            "AUDJPY", AUDJPY_SIMULATION_COST, AUDJPY_CONTRACT_SIZE,
            AUDJPY_DEFAULT_LOT_SIZE, AUDJPY_PIP_SIZE, "JPY",
        ),
    )
}


def simulation_costs() -> dict[str, Decimal]:
    """Return a mutable copy so callers cannot mutate the registry by accident."""
    return {
        symbol: instrument.simulation_cost
        for symbol, instrument in INSTRUMENT_REGISTRY.items()
    }


def register_instrument(
    symbol: str,
    simulation_cost: Decimal | str,
    *,
    contract_size: Decimal | str = Decimal("100000"),
    default_lot_size: Decimal | str = Decimal("0.10"),
    pip_size: Decimal | str | None = None,
) -> Instrument:
    """Add a future instrument after its owner-adjustable cost is specified."""
    normalized = symbol.strip().upper()
    if not normalized.isalnum():
        raise ValueError("Instrument symbols may contain only letters and digits.")
    if normalized in INSTRUMENT_REGISTRY:
        raise ValueError(f"Instrument {normalized} is already registered.")
    cost = Decimal(str(simulation_cost))
    size = Decimal(str(contract_size))
    default_lots = Decimal(str(default_lot_size))
    pip = Decimal(str(pip_size)) if pip_size is not None else (
        Decimal("0.01") if normalized.endswith("JPY") else Decimal("0.0001")
    )
    if not cost.is_finite() or cost < 0:
        raise ValueError("Simulation cost must be a finite non-negative number.")
    if not size.is_finite() or size <= 0:
        raise ValueError("Contract size must be a finite number greater than 0.")
    if not default_lots.is_finite() or default_lots <= 0:
        raise ValueError("Default lot size must be a finite number greater than 0.")
    if not pip.is_finite() or pip <= 0:
        raise ValueError("Pip size must be a finite number greater than 0.")
    if len(normalized) < 6:
        raise ValueError("Instrument symbols must include a quote currency.")
    instrument = Instrument(
        normalized, cost, size, default_lots, pip, normalized[-3:]
    )
    INSTRUMENT_REGISTRY[normalized] = instrument
    return instrument
