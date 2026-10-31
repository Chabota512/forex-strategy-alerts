from __future__ import annotations

from decimal import Decimal
import json
import threading
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from lab.instruments import INSTRUMENT_REGISTRY
from lab.llm_client import LLMProposalResponse
from lab.server import (
    TRADE_COMMENT_PROMPT_RULES,
    ControlRoomService,
)
from lab.simulation import SimulationSession, SimulationSettings


def _record(index: int, open_: str, high: str, low: str, close: str) -> dict[str, Any]:
    return {
        "time": f"2025-01-01T00:{index:02d}:00Z",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    }


def _alert(symbol: str, *, low: str, high: str) -> dict[str, Any]:
    return {
        "alert_id": f"{symbol.lower()}-trade",
        "symbol": symbol,
        "direction": "BUY",
        "alert_type": "BUY-A",
        "strength_label": "STRONG",
        "candle": {
            "time": "2025-01-01T00:00:00Z",
            "open": "100" if symbol == "XAUUSD" else "150",
            "high": high,
            "low": low,
            "close": "100" if symbol == "XAUUSD" else "150",
        },
        "previous_candle": {
            "time": "2024-12-31T23:59:00Z",
            "open": "99",
            "high": "100",
            "low": "98",
            "close": "99",
        },
    }


def _trade_note_service(output_dir: Path) -> ControlRoomService:
    service = object.__new__(ControlRoomService)
    service.output_dir = output_dir
    service.cache_lock = threading.RLock()
    return service


def _snapshot(trade_id: str = "trade-note-1") -> dict[str, Any]:
    return {
        "sim_trade_id": trade_id,
        "sim_outcome": "LOSS",
        "sim_net_pnl": "-12.5",
        "sim_r_after_costs": "-1.25",
        "r_multiple_achieved": "-1",
        "symbol": "XAUUSD",
        "tf": "M15",
        "time": "2025-01-01T10:15:00Z",
        "direction": "SELL",
        "variant": "A",
        "strength": "STRONG",
        "cct": "DOWN",
        "cst": "DOWN",
        "wp_state": "up-pressure",
        "mtf_trend_agree": False,
        "alert_candle_ohlc": {
            "open": "2700.0",
            "high": "2702.0",
            "low": "2698.0",
            "close": "2701.0",
        },
        "prev_candle_ohlc": {
            "open": "2699.0",
            "high": "2700.0",
            "low": "2697.0",
            "close": "2699.5",
        },
        "raw_candle_series": [{"open": "ignored"}],
    }


def _proposal(comment: str) -> LLMProposalResponse:
    return LLMProposalResponse(
        diff_json=json.dumps({"rationale": comment}),
        rationale=comment,
        usage={"input": 12, "output": 20, "total": 32},
        provider="gemini",
        key_index=1,
        attempts=[{"status": "succeeded"}],
    )


class TradeAccountingTests(unittest.TestCase):
    def test_instrument_lot_defaults_and_contract_math(self) -> None:
        self.assertEqual(
            SimulationSettings.from_payload(None, symbol="XAUUSD").lot_size,
            Decimal("0.01"),
        )
        self.assertEqual(
            SimulationSettings.from_payload(None, symbol="GBPUSD").lot_size,
            Decimal("0.10"),
        )
        self.assertEqual(INSTRUMENT_REGISTRY["XAUUSD"].contract_size, 100)
        self.assertEqual(INSTRUMENT_REGISTRY["XAUUSD"].pip_size, Decimal("0.10"))
        self.assertEqual(INSTRUMENT_REGISTRY["GBPUSD"].contract_size, 100000)
        self.assertEqual(INSTRUMENT_REGISTRY["GBPUSD"].pip_size, Decimal("0.0001"))
        self.assertEqual(INSTRUMENT_REGISTRY["USDJPY"].pip_size, Decimal("0.01"))

    def test_fixed_lot_pnl_and_pips_follow_contract_size(self) -> None:
        scenarios = [
            (
                "XAUUSD",
                [
                    _record(0, "100", "101", "99", "100"),
                    _record(1, "100", "104", "99", "103"),
                ],
                _alert("XAUUSD", low="99", high="101"),
                0.01,
                4.0,
                40.0,
                "USD",
            ),
            (
                "USDJPY",
                [
                    _record(0, "150", "151", "149", "150"),
                    _record(1, "150", "154", "149", "153"),
                ],
                _alert("USDJPY", low="149", high="151"),
                0.10,
                40000.0,
                400.0,
                "JPY",
            ),
        ]
        for symbol, records, alert, lots, expected_pnl, expected_pips, currency in scenarios:
            with self.subTest(symbol=symbol):
                session = SimulationSession(
                    records,
                    symbol=symbol,
                    settings={
                        "sizingMode": "LOTS",
                        "lotSize": lots,
                        "exitModel": "swept_level_atr",
                        "rewardMultiple": 2,
                    },
                )
                session.process_candle(0)
                session.queue_alert(0, alert)
                session.process_candle(1)

                trade = session.trades_at(1)[0]
                instrument = INSTRUMENT_REGISTRY[symbol]
                expected_price_move = 4.0
                self.assertEqual(trade["lotSize"], lots)
                self.assertEqual(
                    trade["positionSize"],
                    lots * float(instrument.contract_size),
                )
                self.assertEqual(trade["priceMove"], expected_price_move)
                self.assertEqual(trade["pipPnl"], expected_pips)
                self.assertEqual(
                    trade["grossPnl"],
                    lots * float(instrument.contract_size) * expected_price_move,
                )
                self.assertEqual(trade["grossPnl"], expected_pnl)
                self.assertEqual(trade["pnlCurrency"], currency)
                self.assertEqual(trade["outcome"], "WIN")

    def test_trade_note_uses_snapshot_fields_and_caches_provider_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            snapshot = _snapshot()
            (output_dir / "snapshots.jsonl").write_text(
                json.dumps(snapshot) + "\n", encoding="utf-8"
            )
            service = _trade_note_service(output_dir)
            note = (
                "The short closed at its stop after price moved above the entry area. "
                "Downward CCT and CST aligned, while wick pressure was upward."
            )
            with patch(
                "lab.server.llm_client.propose", return_value=_proposal(note)
            ) as propose:
                first = service.trade_comment("trade-note-1")
                second = service.trade_comment("trade-note-1")

            self.assertTrue(first["available"])
            self.assertFalse(first["cached"])
            self.assertEqual(first["comment"], note)
            self.assertEqual(
                first["tokenUsage"],
                {
                    "promptTokens": 12,
                    "responseTokens": 20,
                    "totalTokens": 32,
                },
            )
            self.assertTrue(second["cached"])
            self.assertEqual(second["comment"], note)
            propose.assert_called_once()
            prompt = propose.call_args.args[0]
            self.assertIn(TRADE_COMMENT_PROMPT_RULES, prompt)
            self.assertIn('"alert_candle_ohlc"', prompt)
            self.assertNotIn('"raw_candle_series"', prompt)
            self.assertIn("raw candle series", prompt)

    def test_trade_note_rejects_instruction_probability_and_short_outputs(self) -> None:
        invalid_notes = (
            "The trade closed at its stop. It should have been avoided.",
            "Price moved above entry. The loss was likely.",
            "The trade closed at the stop.",
        )
        for note in invalid_notes:
            with self.subTest(note=note), tempfile.TemporaryDirectory() as directory:
                output_dir = Path(directory)
                (output_dir / "snapshots.jsonl").write_text(
                    json.dumps(_snapshot()) + "\n", encoding="utf-8"
                )
                service = _trade_note_service(output_dir)
                with patch(
                    "lab.server.llm_client.propose", return_value=_proposal(note)
                ):
                    result = service.trade_comment("trade-note-1")
                self.assertFalse(result["available"])
                self.assertIsNone(result["comment"])
                self.assertFalse((output_dir / "ai_cache.json").exists())

    def test_trade_note_without_matching_snapshot_does_not_call_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = _trade_note_service(Path(directory))
            with patch("lab.server.llm_client.propose") as propose:
                result = service.trade_comment("missing")
            self.assertFalse(result["available"])
            propose.assert_not_called()
