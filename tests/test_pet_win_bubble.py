"""Speech-bubble toggle: nothing but real alerts may show while talk is off."""
import unittest
from unittest.mock import Mock

from pet_win import Pet


class BubbleTests(unittest.TestCase):
    def pet(self, talk):
        pet = Pet.__new__(Pet)
        pet.state = {"config": {"pet_talk": talk}}
        pet.cv, pet.bubble_until = Mock(), None
        return pet

    def test_talk_off_hushes_non_alert_lines(self):
        pet = self.pet(False)
        self.assertTrue(pet.hush())   # the throw scream checks this, so it cannot get stuck on screen
        pet.say("착지!", 1.2)
        pet.cv.create_text.assert_not_called()
        self.assertIsNone(pet.bubble_until)

    def test_talk_on_is_not_hushed(self):
        self.assertFalse(self.pet(True).hush())


if __name__ == "__main__":
    unittest.main()
