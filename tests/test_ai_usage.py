"""AI usage readers against fake Claude Code / Codex logs (no real home folder, no network)."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ["FIGMON_HOME"] = tempfile.mkdtemp()
import server  # noqa: E402


def stamp(offset=0):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + offset))


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def write(self, rel, rows):
        f = self.tmp / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json \"usage\" \"token_count\"\n", encoding="utf-8")

    def test_claude_counts_each_reply_once_and_skips_other_days(self):
        use = {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 9999}
        self.write("claude/projects/p/s.jsonl", [
            {"timestamp": stamp(), "message": {"id": "a", "usage": use}},
            {"timestamp": stamp(), "message": {"id": "a", "usage": use}},          # same reply, repeated line
            {"timestamp": stamp(-3 * 86400), "message": {"id": "b", "usage": use}},   # an earlier day
        ])
        (self.tmp / "status.json").write_text(json.dumps({"rate_limits": {
            "five_hour": {"used_percentage": 85, "resets_at": time.time() + 600},
            "seven_day": {"used_percentage": 40, "resets_at": time.time() - 5}}}), encoding="utf-8")
        self.assertEqual(server.claude_usage(self.tmp / "claude", self.tmp / "status.json"),
                         (115, [("5시간", 15.0), ("주간", 100.0)]))

    def test_claude_without_status_file_has_tokens_only(self):
        self.write("claude/projects/p/s.jsonl", [{"timestamp": stamp(), "message": {"id": "a", "usage": {"output_tokens": 7}}}])
        self.assertEqual(server.claude_usage(self.tmp / "claude", self.tmp / "none.json"), (7, []))

    def test_codex_tokens_and_latest_limits(self):
        def ev(total, used):
            return {"timestamp": stamp(), "payload": {"type": "token_count", "info": {
                "total_token_usage": {"total_tokens": total},
                "last_token_usage": {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 10}},
                "rate_limits": {"primary": {"used_percent": used, "window_minutes": 300, "resets_at": time.time() + 60},
                                "secondary": {"used_percent": 18.0, "window_minutes": 10080, "resets_at": time.time() + 60}}}}
        self.write("codex/sessions/2026/11/10/r.jsonl", [ev(50, 10.0), ev(50, 10.0), ev(100, 14.0),
                                                         {"timestamp": stamp(), "payload": {"type": "token_count", "info": None, "rate_limits": None}}])
        self.assertEqual(server.codex_usage(self.tmp / "codex"), (100, [("5시간", 86.0), ("주간", 82.0)]))

    def test_missing_tools_and_lines(self):
        self.assertIsNone(server.claude_usage(self.tmp / "nope"))
        self.assertIsNone(server.codex_usage(self.tmp / "nope"))
        out = server.ai_usage([("Claude", (1663828, [("5시간", 15.0)])), ("Codex", None), ("X", (0, []))])
        self.assertEqual(out["lines"], ["Claude 오늘 166.4만 토큰 · 5시간 한도 15% 남음"])
        self.assertIn("Claude 5시간", out["low"])

    def test_air_pets(self):
        self.assertTrue({6, 16, 92, 149} <= server.AIR)       # 리자몽·구구·고오스·망나뇽
        self.assertFalse({1, 25, 94} & server.AIR)             # 이상해씨·피카츄·팬텀


if __name__ == "__main__":
    unittest.main()
