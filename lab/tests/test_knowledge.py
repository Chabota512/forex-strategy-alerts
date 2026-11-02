from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from lab import evolve
from lab.knowledge import admit_candidate, top_admitted_knowledge

SEED_PATH = Path(__file__).parents[1] / "specs" / "seed_v1.5.0.json"


def test_admission_requires_sample_floor_positive_lift_and_provenance() -> None:
    with TemporaryDirectory() as temp_dir:
        knowledge_dir = Path(temp_dir)
        low_sample = admit_candidate(
            "rule",
            {"name": "small sample", "source": "evidence"},
            sample_count=29,
            expectancy=0.4,
            baseline_expectancy=0.1,
            provenance={"source": "fixture", "window": "2025"},
            knowledge_dir=knowledge_dir,
        )
        no_lift = admit_candidate(
            "calibration",
            {"name": "no lift", "source": "evidence"},
            sample_count=30,
            expectancy=0.1,
            baseline_expectancy=0.1,
            provenance={"source": "fixture", "window": "2025"},
            knowledge_dir=knowledge_dir,
        )
        admitted = admit_candidate(
            "rule",
            {"name": "gold-session-filter", "source": "evidence"},
            sample_count=40,
            expectancy=0.35,
            baseline_expectancy=0.1,
            provenance={"source": "fixture", "window": "2025-01..2025-09"},
            knowledge_dir=knowledge_dir,
        )

        assert low_sample["status"] == "local"
        assert "sample_floor" in low_sample["entry"]["reasons"]
        assert no_lift["status"] == "local"
        assert "expectancy_not_above_baseline" in no_lift["entry"]["reasons"]
        assert admitted["status"] == "admitted"
        assert top_admitted_knowledge(knowledge_dir=knowledge_dir)[0]["key"] == admitted["key"]


def test_rejection_lessons_require_owner_lesson_ledger_and_prompt_consumes_admitted() -> None:
    with TemporaryDirectory() as temp_dir:
        knowledge_dir = Path(temp_dir)
        candidate = {
            "lessonId": "lesson-verified",
            "lesson": "Do not repeat the rejected threshold.",
            "source": "owner",
        }
        ungoverned = admit_candidate(
            "rejection-lesson",
            candidate,
            sample_count=35,
            expectancy=0.3,
            baseline_expectancy=0.1,
            provenance={"source": "owner lesson ledger", "window": "2025"},
            lesson_ledger=[],
            knowledge_dir=knowledge_dir,
        )
        admitted = admit_candidate(
            "rejection-lesson",
            candidate,
            sample_count=35,
            expectancy=0.3,
            baseline_expectancy=0.1,
            provenance={"source": "owner lesson ledger", "window": "2025"},
            lesson_ledger=[{"lesson_id": "lesson-verified"}],
            knowledge_dir=knowledge_dir / "governed",
        )
        spec = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        prompt = evolve.build_prompt(
            spec,
            evolve.options_for_spec(spec),
            {"snapshot_count": 0, "groups": []},
            [],
            knowledge_dir=knowledge_dir / "governed",
        )

        assert ungoverned["status"] == "local"
        assert "lesson_not_in_ledger" in ungoverned["entry"]["reasons"]
        assert admitted["status"] == "admitted"
        assert "Do not repeat the rejected threshold." in prompt
        assert "TOP ADMITTED KNOWLEDGE COMMONS" in prompt
