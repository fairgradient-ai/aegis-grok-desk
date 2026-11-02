"""Gate fixture: one test so the suite step has something to count."""
from __future__ import annotations

import unittest


class SmokeTest(unittest.TestCase):
    def test_true(self) -> None:
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
