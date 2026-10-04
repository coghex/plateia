# Throwaway negative proof for CI-1 (#5): never merged.
import unittest


class CiNegativeProof(unittest.TestCase):
    def test_deliberately_fails(self):
        self.assertEqual(1, 2, "deliberate failure: CI must report it")
