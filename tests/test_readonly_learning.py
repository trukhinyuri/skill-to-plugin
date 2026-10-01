from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from skill_to_plugin.learning import lessons


class ReadOnlyStatusTests(unittest.TestCase):
    def test_status_does_not_initialize_or_repair_store(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp" if Path("/private/tmp").exists() else None) as tmp:
            store = Path(tmp) / ".skill-to-plugin"
            self.assertEqual(lessons(store), [])
            self.assertFalse(store.exists())
            store.mkdir()
            with self.assertRaises(ValueError):
                lessons(store)
            self.assertEqual(list(store.iterdir()), [])
