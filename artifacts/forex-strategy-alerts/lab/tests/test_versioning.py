import copy
import json
import tempfile
import unittest
from pathlib import Path

from lab.backtest import load_spec
from lab.export import render_spec_text
from lab.versioning import (
    VersioningError,
    build_candidate,
    next_patch_version,
    promote_candidate,
    update_candidate_review,
    write_candidate,
)


SEED_PATH = Path(__file__).parents[1] / "specs" / "seed_v1.5.0.json"


class VersionNumberTests(unittest.TestCase):
    def test_next_patch_increments_only_patch(self):
        self.assertEqual(next_patch_version("1.5.0"), "1.5.1")
        self.assertEqual(next_patch_version("2.9.12"), "2.9.13")

    def test_invalid_semver_is_rejected(self):
        for version in ("1.5", "v1.5.0", "1.05.0", "one.two.three"):
            with self.subTest(version=version):
                with self.assertRaises(VersioningError):
                    next_patch_version(version)


class CandidateSafetyTests(unittest.TestCase):
    def setUp(self):
        self.seed = load_spec(SEED_PATH)
        self.seed["status"] = "owner_approved_unfrozen"
        self.seed["frozen"] = False

    def test_candidate_changes_only_allowlisted_fields(self):
        candidate, changes = build_candidate(
            self.seed,
            changes=[{"path": "cct.window", "value": 17}],
            summary="Test a slightly wider descriptive context window.",
        )
        self.assertEqual(candidate["version"], "1.5.1")
        self.assertEqual(candidate["status"], "candidate_pending_review")
        self.assertFalse(candidate["frozen"])
        self.assertEqual(candidate["cct"]["window"], 17)
        self.assertEqual(candidate["trigger"], self.seed["trigger"])
        self.assertEqual(changes[0]["old_value"], 15)

    def test_trigger_and_validation_cannot_be_changed(self):
        for path, value in (
            ("trigger.strict_wick_break", False),
            ("reward.validation.training_months", 8),
            ("reward.floors.minimum_months_with_alerts", 1),
        ):
            with self.subTest(path=path):
                with self.assertRaises(VersioningError):
                    build_candidate(
                        self.seed,
                        changes=[{"path": path, "value": value}],
                        summary="Must be rejected.",
                    )

    def test_invalid_parameter_ranges_and_non_changes_are_rejected(self):
        for path, value in (
            ("cct.window", 4),
            ("cct.swing_neighbors", 0),
            ("cst.break_basis", "open"),
            ("reward.thresholds.GBPUSD.d_min", "0"),
            ("cct.window", 15),
        ):
            with self.subTest(path=path, value=value):
                with self.assertRaises(VersioningError):
                    build_candidate(
                        self.seed,
                        changes=[{"path": path, "value": value}],
                        summary="Invalid candidate.",
                    )

    def test_candidate_rejects_frozen_baseline(self):
        frozen = copy.deepcopy(self.seed)
        frozen["frozen"] = True
        with self.assertRaisesRegex(VersioningError, "frozen"):
            build_candidate(
                frozen,
                changes=[{"path": "cct.window", "value": 17}],
                summary="Not allowed.",
            )


class ExportAndPromotionTests(unittest.TestCase):
    def setUp(self):
        self.seed = load_spec(SEED_PATH)

    def test_text_export_contains_version_settings_and_safety_notes(self):
        exported = render_spec_text(self.seed)
        self.assertIn("Version: 1.5.0", exported)
        self.assertIn("D_min=0.0010 / A_max=0.0008", exported)
        self.assertIn("Training/validation split: 9/3 of 12 months", exported)
        self.assertIn("No orders, auto-trading, profit claims", exported)
        self.assertIn("Settings fingerprint:", exported)

    def test_promotion_archives_baseline_and_leaves_seed_unchanged(self):
        original_seed = SEED_PATH.read_text(encoding="utf-8")
        baseline = copy.deepcopy(self.seed)
        baseline["status"] = "owner_approved_unfrozen"
        baseline["frozen"] = False
        candidate, _ = build_candidate(
            baseline,
            changes=[{"path": "cct.window", "value": 17}],
            summary="Controlled test candidate.",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_path = write_candidate(
                candidate,
                proposals_dir=root / "proposals",
            )
            archive = promote_candidate(
                candidate,
                baseline=baseline,
                champion_path=root / "specs" / "champion.json",
                versions_dir=root / "versions",
                changelog_path=root / "versions" / "changelog.md",
                summary="Controlled test candidate.",
                validation_score="123",
                improvement_percent="12.30",
                floors_status="PASS",
            )
            promoted = json.loads(
                (root / "specs" / "champion.json").read_text(encoding="utf-8")
            )
            archived = json.loads(archive.read_text(encoding="utf-8"))
            changelog = (root / "versions" / "changelog.md").read_text(
                encoding="utf-8"
            )
            update_candidate_review(
                candidate_path,
                decision="promoted",
                reason="Test-only approval.",
            )
            reviewed_candidate = json.loads(candidate_path.read_text(encoding="utf-8"))

        self.assertEqual(promoted["version"], "1.5.1")
        self.assertEqual(promoted["status"], "owner_approved_unfrozen")
        self.assertFalse(promoted["frozen"])
        self.assertEqual(archived["version"], "1.5.0")
        self.assertIn("manually approved", changelog.lower())
        self.assertEqual(reviewed_candidate["review"]["decision"], "promoted")
        self.assertEqual(SEED_PATH.read_text(encoding="utf-8"), original_seed)


if __name__ == "__main__":
    unittest.main()
