"""A save file that can't be read must never be silently replaced by an empty one (that wiped the Pokédex)."""
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ["FIGMON_HOME"] = tempfile.mkdtemp()
import server  # noqa: E402

SAVED = {"dex": [{"line": "722", "species": [724, "모크나이퍼"], "level": 36, "finished": 1.0}], "xp_curve": 1, "evo_v": 2}


class LoadStateTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self._file, server.STATE_FILE = server.STATE_FILE, self.home / "state.json"

    def tearDown(self):
        server.STATE_FILE = self._file

    def test_bom_saved_by_notepad_still_loads(self):
        server.STATE_FILE.write_bytes(b"\xef\xbb\xbf" + json.dumps(SAVED, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(len(server.load_state()["dex"]), 1)

    def test_unreadable_file_is_backed_up_before_starting_fresh(self):
        server.STATE_FILE.write_text('{"dex": [broken', encoding="utf-8")
        self.assertEqual(server.load_state()["dex"], [])
        backups = list(self.home.glob("state.broken-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), '{"dex": [broken')


if __name__ == "__main__":
    unittest.main()
