"""Movement regression tests without a GUI, network, or user save data."""
import unittest
from unittest.mock import Mock

from pet_win import Pet


class MovementTests(unittest.TestCase):
    def pet(self, x, target):
        pet = Pet.__new__(Pet)
        pet.dpi = pet.size = 1.0
        pet.W, pet.H = 300, 320
        pet.x, pet.y = x, 720
        pet.cv = Mock()  # resize only changes canvas dimensions
        pet.frames, pet.cur_key = {}, None
        pet.mode, pet.calm = 'neutral', False
        pet.target, pet.speed = target, 55.0
        pet.vx = pet.look_until = pet.rest_until = 0.0
        pet.next_dust = pet.next_look = float('inf')
        return pet

    def arrive(self, pet):
        for step in range(1800):
            pet.think(10 + step / 60, 960, 0, 1920, 1 / 60)
            pet.x = min(max(pet.x, -pet.W * .3), 1920 - pet.W * .7)
            if pet.target is None:
                return
        self.fail('Pet never reaches its destination and remains stuck at the edge')

    def test_growing_near_right_edge_finishes_walk(self):
        pet = self.pet(1700, 1705)
        pet.resize(1.6)
        self.arrive(pet)
        self.assertFalse(pet.walking)
        # After resting, movement can begin again toward a reachable cursor.
        pet.mode = 'warning'
        pet.think(pet.rest_until + 1, 600, 0, 1920, 1 / 60)
        self.assertLess(pet.target, pet.x)

    def test_shrinking_near_left_edge_finishes_walk(self):
        pet = self.pet(-150, -160)
        pet.resize(1.6)
        pet.resize(.6)
        self.arrive(pet)

    def test_cursor_destination_beyond_screen_finishes_walk(self):
        pet = self.pet(1600, 2000)
        self.arrive(pet)

    def test_in_bounds_destination_is_preserved(self):
        pet = self.pet(900, 1000)
        self.arrive(pet)
        self.assertAlmostEqual(pet.x, 1000, delta=2)

    def test_blocked_walk_turns_around_after_two_seconds(self):
        pet = self.pet(1600, 1700)
        for step in range(200):   # 무언가가 계속 제자리에 붙잡아 둠
            pet.think(10 + step / 60, 960, 0, 1920, 1 / 60)
            pet.x = 1600
        self.assertLess(pet.target, 1600)   # 반대쪽으로 목적지를 바꿈


if __name__ == '__main__':
    unittest.main()
