"""Exercise the public CLI across a real skill, behavior repair, learning and rollback."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PublicWorkflowTests(unittest.TestCase):
    def test_build_update_learn_promote_and_rollback_via_cli(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp" if Path("/private/tmp").exists() else None) as directory:
            project = Path(directory)
            baseline, candidate = project / "baseline", project / "candidate"
            def cli(*args):
                command = [sys.executable, str(ROOT / "scripts/stp.py"), *map(str, args)]
                result = subprocess.run(command, text=True, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)
            source = ROOT / "examples/normalize-text"
            cli("build", source, "--name", "text-tools", "--output", baseline)
            worker = baseline / "skills/normalize-text/scripts/normalize.py"
            worker.write_text("def normalize(text):\n    return ' '.join(p for p in text.strip().split(' ') if p)\n")
            before = worker.read_bytes()
            cli("build", source, "--name", "text-tools", "--output", candidate,
                "--existing", baseline, "--plugin-version", "0.2.0")
            cases = []
            for name, text, expected in [("ordinary", " a  b ", "a b"),
                                         ("unicode-tab", " Привет\t世界 ", "Привет 世界"),
                                         ("empty", "", "")]:
                check = "import runpy; f=runpy.run_path('skills/normalize-text/scripts/normalize.py')['normalize']; assert f(%r)==%r" % (text, expected)
                cases.append({"id": name, "argv": [sys.executable, "-B", "-c", check], "timeout": 5})
            suite = project / "suite.json"
            suite.write_text(json.dumps({"cases": cases}))
            evaluation = cli("learn", "evaluate", "--project", project, "--baseline", baseline,
                             "--candidate", candidate, "--suite", suite, "--allow-exec")
            self.assertTrue(evaluation["decision"]["eligible"])
            self.assertEqual(evaluation["decision"]["baseline_score"], 2)
            self.assertEqual(evaluation["decision"]["candidate_score"], 3)
            evidence = project / "observed.json"
            evidence.write_text(json.dumps(evaluation["decision"]))
            event = project / "event.json"
            event.write_text(json.dumps({"kind": "failure", "summary": "Baseline mishandles Unicode whitespace",
                                        "evidence": {"path": "observed.json"},
                                        "proposed_lesson": "Use Unicode whitespace splitting when this normalizer's contract requires it."}))
            cli("learn", "record", "--project", project, "--event", event)
            status = cli("learn", "status", "--project", project)
            self.assertEqual(status["proposedLessons"][0]["status"], "proposed")
            self.assertEqual(worker.read_bytes(), before)
            promotion = cli("learn", "promote", "--project", project, "--baseline", baseline,
                            "--candidate", candidate, "--evaluation-id", evaluation["id"])
            self.assertNotEqual(worker.read_bytes(), before)
            cli("learn", "rollback", "--project", project, "--baseline", baseline,
                "--promotion-id", promotion["id"])
            self.assertEqual(worker.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
