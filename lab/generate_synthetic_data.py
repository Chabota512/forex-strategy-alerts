"""Generate repeatable synthetic OHLC candles for software tests only."""

from __future__ import annotations

import argparse
import csv
import random
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

INSTRUMENTS = {
    "GBPUSD": {
        "start": Decimal("1.27000"),
        "tick": Decimal("0.00001"),
        "vol": Decimal("0.00030"),
    },
    "USDJPY": {
        "start": Decimal("149.500"),
        "tick": Decimal("0.001"),
        "vol": Decimal("0.030"),
    },
    "XAUUSD": {
        "start": Decimal("2500.00"),
        "tick": Decimal("0.01"),
        "vol": Decimal("0.55"),
    },
}
TIMEFRAME_MINUTES = {"M5": 5, "M15": 15}
DATA_DIR = Path(__file__).resolve().parent / "data" / "synthetic"
FIELDS = ("time", "open", "high", "low", "close", "source", "generator_seed")


def month_window(end_date: date, total_months: int = 12) -> tuple[date, date]:
    """Return the first day of the first month through the supplied end date."""
    if total_months < 1:
        raise ValueError("total_months must be positive")
    end_ordinal = end_date.year * 12 + end_date.month - 1
    start_ordinal = end_ordinal - total_months + 1
    start_year, start_month_index = divmod(start_ordinal, 12)
    return date(start_year, start_month_index + 1, 1), end_date


def format_price(value: float, tick: Decimal) -> str:
    return str(Decimal(str(value)).quantize(tick, rounding=ROUND_HALF_UP))


def iter_weekdays(start: date, end: date):
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def generate_file(
    symbol: str,
    timeframe: str,
    start: date,
    end: date,
    seed: int,
    output_dir: Path,
) -> tuple[Path, int]:
    settings = INSTRUMENTS[symbol]
    tick = settings["tick"]
    base_volatility = float(settings["vol"])
    timeframe_multiplier = 1.0 if timeframe == "M5" else 1.7
    volatility = base_volatility * timeframe_multiplier
    interval = TIMEFRAME_MINUTES[timeframe]
    rng = random.Random(f"{seed}:{symbol}:{timeframe}")
    price = float(settings["start"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{symbol}_{timeframe}.csv"
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing synthetic data: {path}")
    rows = 0

    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for day in iter_weekdays(start, end):
            current_time = datetime.combine(day, time.min, tzinfo=timezone.utc)
            for _ in range((24 * 60) // interval):
                open_price = price
                close_price = max(
                    float(tick),
                    open_price + rng.gauss(0, volatility),
                )
                upper_wick = abs(rng.gauss(volatility * 0.65, volatility * 0.2))
                lower_wick = abs(rng.gauss(volatility * 0.65, volatility * 0.2))
                high_price = max(open_price, close_price) + upper_wick
                low_price = max(
                    float(tick),
                    min(open_price, close_price) - lower_wick,
                )
                writer.writerow(
                    {
                        "time": current_time.isoformat(timespec="seconds").replace(
                            "+00:00", "Z"
                        ),
                        "open": format_price(open_price, tick),
                        "high": format_price(high_price, tick),
                        "low": format_price(low_price, tick),
                        "close": format_price(close_price, tick),
                        "source": "synthetic",
                        "generator_seed": str(seed),
                    }
                )
                rows += 1
                price = close_price
                current_time += timedelta(minutes=interval)
    return path, rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create synthetic weekday OHLC data for software tests only."
    )
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=datetime.now(timezone.utc).date(),
        help="Inclusive UTC end date in YYYY-MM-DD format",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=140,
        help="Repeatable random seed (default: 140)",
    )
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args(argv)

    start, end = month_window(args.end_date)
    print("SYNTHETIC DATA ONLY — not historical prices and not for trading decisions.")
    print(
        f"Calendar range: {start.isoformat()} through {end.isoformat()} "
        f"(12 month buckets, UTC weekdays)"
    )
    print(f"Generator seed: {args.seed}")
    for symbol in INSTRUMENTS:
        for timeframe in TIMEFRAME_MINUTES:
            path, rows = generate_file(
                symbol,
                timeframe,
                start,
                end,
                args.seed,
                args.output_dir,
            )
            print(f"{path.name}: {rows:,} candles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
