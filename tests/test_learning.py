"""Behavioral regression coverage for measured, project-local learning."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from skill_to_plugin.learning import evaluate, lessons, promote, record_event, rollback


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.store = self.project / ".skill-to-plugin"
        self.baseline = self.project / "baseline"
        self.candidate = self.project / "candidate"
        self.baseline.mkdir(); self.candidate.mkdir()
        (self.baseline / "behavior.txt").write_text("old behavior\n")
        (self.candidate / "behavior.txt").write_text("correct behavior\n")
        self.suite = self.project / "suite.json"
        self.write_suite([
            self.case("preserves-common-behavior", "'behavior' in text"),
            self.case("fixes-observed-failure", "text == 'correct behavior\\n'"),
        ])

    def tearDown(self):
        self.temp.cleanup()

    def case(self, name, assertion, **options):
        code = "from pathlib import Path; text = Path('behavior.txt').read_text(); assert " + assertion
        return {"id": name, "argv": [sys.executable, "-c", code], **options}

    def write_suite(self, cases):
        self.suite.write_text(json.dumps({"cases": cases}))

    def evaluate(self):
        return evaluate(self.baseline, self.candidate, self.suite, self.store, allow_exec=True)

    def test_observations_form_inert_proposals_with_evidence(self):
        first = record_event(self.store, {
            "kind": "failure", "summary": "The output omitted the observed correction",
            "evidence": {"path": "baseline/behavior.txt"},
            "proposed_lesson": "Compare the observed correction before emitting output",
        })
        second = record_event(self.store, {
            "kind": "correction", "summary": "A second output needed the same correction",
            "evidence": "Observed failing test: fixes-observed-failure",
            "proposed_lesson": first["proposed_lesson"],
        })
        proposals = lessons(self.store)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["status"], "proposed")
        self.assertEqual(set(proposals[0]["evidence_ids"]), {first["id"], second["id"]})
        self.assertEqual(first["evidence"]["bytes"], len(b"old behavior\n"))
        self.assertEqual((self.baseline / "behavior.txt").read_text(), "old behavior\n")
        with self.assertRaises(ValueError):
            record_event(self.store, {"kind": "failure", "summary": "missing proof", "evidence": ""})

    def test_commands_require_explicit_authorization(self):
        sentinel = self.project / "executed"
        self.write_suite([{"id": "writes", "argv": [sys.executable, "-c", f"open({str(sentinel)!r}, 'w').write('x')"]}])
        with self.assertRaises(PermissionError):
            evaluate(self.baseline, self.candidate, self.suite, self.store)
        self.assertFalse(sentinel.exists())

    def test_verified_improvement_promotes_and_rolls_back(self):
        evaluation = self.evaluate()
        self.assertTrue(evaluation["decision"]["eligible"])
        self.assertEqual(evaluation["decision"]["improved_cases"], ["fixes-observed-failure"])
        self.assertEqual(evaluation["decision"]["regressions"], [])
        self.assertFalse(evaluation["results"]["baseline"][1]["passed"])
        self.assertTrue(evaluation["results"]["candidate"][1]["passed"])
        promotion = promote(self.baseline, self.candidate, self.store, evaluation["id"])
        self.assertEqual((self.baseline / "behavior.txt").read_text(), "correct behavior\n")
        backup = self.store / "promotions" / promotion["id"] / "backup" / "behavior.txt"
        self.assertEqual(backup.read_text(), "old behavior\n")
        restored = rollback(self.baseline, self.store, promotion["id"])
        self.assertEqual((self.baseline / "behavior.txt").read_text(), "old behavior\n")
        self.assertEqual((self.candidate / "behavior.txt").read_text(), "correct behavior\n")
        self.assertEqual(restored["promotion_id"], promotion["id"])
        self.assertTrue(backup.exists())
        with self.assertRaises(ValueError):
            rollback(self.baseline, self.store, promotion["id"])

    def test_equal_score_is_not_improvement(self):
        (self.baseline / "behavior.txt").write_text("correct behavior\n")
        evaluation = self.evaluate()
        self.assertFalse(evaluation["decision"]["eligible"])
        with self.assertRaises(ValueError):
            promote(self.baseline, self.candidate, self.store, evaluation["id"])

    def test_improvement_cannot_hide_a_regression_even_when_noncritical(self):
        self.write_suite([
            self.case("old-contract", "text.startswith('old')", weight=1, critical=False),
            self.case("fix", "text.startswith('correct')", weight=10),
        ])
        evaluation = self.evaluate()
        self.assertGreater(evaluation["decision"]["candidate_score"], evaluation["decision"]["baseline_score"])
        self.assertEqual(evaluation["decision"]["regressions"], ["old-contract"])
        with self.assertRaises(ValueError):
            promote(self.baseline, self.candidate, self.store, evaluation["id"])
        self.assertEqual((self.baseline / "behavior.txt").read_text(), "old behavior\n")

    def test_evaluation_is_bound_to_current_artifacts_and_suite(self):
        for target in (self.candidate / "behavior.txt", self.baseline / "behavior.txt", self.suite):
            with self.subTest(target=target):
                evaluation = self.evaluate()
                original = target.read_bytes()
                target.write_bytes(original + b" ")
                with self.assertRaises(ValueError):
                    promote(self.baseline, self.candidate, self.store, evaluation["id"])
                target.write_bytes(original)

    def test_replaced_baseline_identity_rejects_stale_overwrite(self):
        evaluation = self.evaluate()
        moved = self.project / "moved-baseline"
        self.baseline.rename(moved)
        shutil.copytree(moved, self.baseline)
        with self.assertRaisesRegex(ValueError, "identity"):
            promote(self.baseline, self.candidate, self.store, evaluation["id"])
        self.assertEqual((self.baseline / "behavior.txt").read_text(), "old behavior\n")

    def test_editing_a_record_cannot_create_successful_evidence(self):
        evaluation = self.evaluate()
        path = self.store / "evaluations" / evaluation["id"] / "record.json"
        value = json.loads(path.read_text())
        value["results"]["baseline"][1]["passed"] = True
        value["decision"]["candidate_score"] = 99999
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "integrity"):
            promote(self.baseline, self.candidate, self.store, evaluation["id"])

    def test_snapshot_mutation_invalidates_evaluation(self):
        self.write_suite([{
            "id": "mutates-plugin",
            "argv": [sys.executable, "-c", "from pathlib import Path; Path('behavior.txt').write_text('changed')"],
        }])
        evaluation = self.evaluate()
        self.assertTrue(evaluation["results"]["candidate"][0]["passed"])
        self.assertFalse(evaluation["unchanged"])
        self.assertFalse(evaluation["decision"]["eligible"])
        self.assertEqual((self.candidate / "behavior.txt").read_text(), "correct behavior\n")

    def test_timeout_and_output_are_bounded_and_count_as_failure(self):
        self.write_suite([
            {"id": "timeout", "argv": [sys.executable, "-c", "import time; time.sleep(2)"], "timeout": 0.05},
            {"id": "output", "argv": [sys.executable, "-c", "print('x' * 200000)"], "timeout": 2},
        ])
        evaluation = self.evaluate()
        cases = evaluation["results"]["candidate"]
        self.assertTrue(cases[0]["timed_out"])
        self.assertTrue(cases[1]["output_limited"])
        self.assertLessEqual(len(cases[1]["output"].encode()), 65536)
        self.assertFalse(any(case["passed"] for case in cases))

    def test_symlinks_and_global_or_unsafe_paths_are_rejected(self):
        (self.candidate / "unsafe").symlink_to(self.baseline / "behavior.txt")
        with self.assertRaisesRegex(ValueError, "Unsafe tree"):
            self.evaluate()
        (self.candidate / "unsafe").unlink()
        linked = self.project / "linked"
        linked.symlink_to(self.candidate, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            evaluate(self.baseline, linked, self.suite, self.store, allow_exec=True)
        with self.assertRaises(ValueError):
            record_event(self.project / "arbitrary", {"kind": "failure", "summary": "x", "evidence": "x"})
        cache = self.project / ".codex"
        cache.mkdir()
        with self.assertRaisesRegex(ValueError, "global"):
            record_event(cache / ".skill-to-plugin", {"kind": "failure", "summary": "x", "evidence": "x"})
        with self.assertRaises(ValueError):
            evaluate(self.project, self.candidate, self.suite, self.store, allow_exec=True)

    def test_modified_promoted_content_and_backup_block_rollback(self):
        evaluation = self.evaluate()
        promotion = promote(self.baseline, self.candidate, self.store, evaluation["id"])
        content = self.baseline / "behavior.txt"
        content.write_text("new user work")
        with self.assertRaises(ValueError):
            rollback(self.baseline, self.store, promotion["id"])
        self.assertEqual(content.read_text(), "new user work")
        content.write_text("correct behavior\n")
        backup = self.store / "promotions" / promotion["id"] / "backup" / "behavior.txt"
        backup.write_text("unverified backup")
        with self.assertRaisesRegex(ValueError, "backup"):
            rollback(self.baseline, self.store, promotion["id"])

    def test_failed_promotion_record_restores_original_output(self):
        evaluation = self.evaluate()
        with patch("skill_to_plugin.learning._write_json", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                promote(self.baseline, self.candidate, self.store, evaluation["id"])
        self.assertEqual((self.baseline / "behavior.txt").read_text(), "old behavior\n")
        self.assertEqual((self.candidate / "behavior.txt").read_text(), "correct behavior\n")
        self.assertFalse((self.store / ".lock").exists())

    def test_bad_suites_are_rejected_before_execution(self):
        for options in ({"timeout": 61}, {"weight": -1}, {"expected_exit": True}, {"critical": "yes"}):
            with self.subTest(options=options):
                self.write_suite([self.case("invalid", "True", **options)])
                with self.assertRaises(ValueError):
                    self.evaluate()
        self.write_suite([self.case("same", "True"), self.case("same", "True")])
        with self.assertRaises(ValueError):
            self.evaluate()

    def test_root_modes_and_snapshot_changes_are_bound_to_evidence(self):
        evaluation = self.evaluate()
        original = self.candidate.stat().st_mode & 0o777
        self.candidate.chmod(0o700 if original != 0o700 else 0o755)
        with self.assertRaisesRegex(ValueError, "content"):
            promote(self.baseline, self.candidate, self.store, evaluation["id"])
        self.candidate.chmod(original)
        snapshot = self.store / "evaluations" / evaluation["id"] / "candidate" / "behavior.txt"
        snapshot.write_text("changed evidence")
        with self.assertRaisesRegex(ValueError, "snapshot"):
            promote(self.baseline, self.candidate, self.store, evaluation["id"])

    def test_store_links_and_wrong_event_types_are_rejected(self):
        actual = self.project / "elsewhere"
        actual.mkdir()
        self.store.symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            lessons(self.store)
        self.store.unlink()
        with self.assertRaises(ValueError):
            record_event(self.store, {"kind": [], "summary": "x", "evidence": "x"})

    def test_concurrent_event_writes_are_distinct_and_readable(self):
        # Initialize directories before introducing concurrent writers.
        lessons(self.store)
        errors = []
        def write(index):
            try:
                record_event(self.store, {"kind": "outcome", "summary": f"result {index}",
                                         "evidence": f"observation {index}", "proposed_lesson": "Check observed output"})
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=write, args=(index,)) for index in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(lessons(self.store)[0]["occurrences"], 8)


if __name__ == "__main__":
    unittest.main()
