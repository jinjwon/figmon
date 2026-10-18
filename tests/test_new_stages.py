"""롱스톤 → 강철톤, 골뱃 → 크로뱃 등: 2세대 이후 진화형이 새로 붙어도 예전 저장이 깨지지 않는지 (사용자 저장 데이터는 건드리지 않음)."""
import os
import tempfile
import unittest

os.environ["FIGMON_HOME"] = tempfile.mkdtemp()
import server  # noqa: E402


def save_with(line, lv, evo_v):
    pet = {"line": line, "xp": 0.0}
    pet["xp"] = server.xp_at(lv, server.growth_of(pet)) + 1
    return {"pet": pet, "events": [], "evo_v": evo_v}


def level(s):
    return server.level_of(s["pet"]["xp"], server.growth_of(s["pet"]))


class NewStagesTests(unittest.TestCase):
    def test_lines_reach_mega(self):
        for key, last in (("95", 208), ("123", 212)):
            line = server.LINES[key]
            self.assertEqual([i for i, _ in line["stages"]], [int(key), last])
            self.assertEqual(line["evo_levels"], [0, 30])   # 통신교환 첫 진화
            self.assertEqual(line["master_lv"], server.MASTER_LV)
            self.assertTrue(server.mega_options(line, 2))

    def test_old_save_keeps_level(self):
        # 1.24 이전(evo_v 0) 저장의 Lv.25 롱스톤: 예전 Lv.20 2단계 규칙으로 Lv.30까지 올려 주면 안 됨
        s = save_with("95", 25, 0)
        server.migrate_evo_levels(s)
        self.assertEqual(level(s), 25)
        self.assertEqual(server.stage_of(level(s), "95"), 1)
        self.assertEqual(s["events"], [])
        self.assertEqual(s["evo_v"], 2)

    def test_past_evo_level_evolves_with_log(self):
        for evo_v in (0, 1):
            s = save_with("123", 33, evo_v)
            server.migrate_evo_levels(s)
            self.assertEqual(level(s), 33)
            self.assertEqual(server.stage_of(33, "123"), 2)
            self.assertEqual(len(s["events"]), 1)
            self.assertIn("핫삼", s["events"][0]["msg"])
            server.migrate_evo_levels(s)   # 두 번 돌려도 기록은 한 번
            self.assertEqual(len(s["events"]), 1)

    def test_added_final_stages(self):
        want = {"41": 169, "81": 462, "108": 463, "111": 464, "114": 465, "116": 230, "137": 474}
        for key, last in want.items():
            line = server.LINES[key]
            self.assertEqual(line["stages"][-1][0], last)
            self.assertEqual(len(line["stages"]), server.OLD_STAGES[key] + (2 if key == "137" else 1))

    def test_two_new_stages_log_from_old_last(self):
        s = save_with("137", 45, 1)   # 폴리곤 → (폴리곤2) → 폴리곤Z
        server.migrate_evo_levels(s)
        self.assertIn("폴리곤 → 폴리곤Z", s["events"][0]["msg"])

    def test_mastered_pet_keeps_old_master_level(self):
        s = save_with("116", 40, 1)   # 예전엔 Lv.36에 완전 성장한 시드라, 이제 킹드라가 Lv.46
        server.migrate_evo_levels(s)
        self.assertEqual(server.master_of(s["pet"]), 36)
        self.assertEqual(server.stage_of(40, "116"), 2)
        self.assertEqual(s["events"], [])

    def test_growing_pet_gets_new_master_level(self):
        s = save_with("116", 30, 1)
        server.migrate_evo_levels(s)
        self.assertNotIn("grad_lv", s["pet"])
        self.assertEqual(server.master_of(s["pet"]), 46)


if __name__ == "__main__":
    unittest.main()
