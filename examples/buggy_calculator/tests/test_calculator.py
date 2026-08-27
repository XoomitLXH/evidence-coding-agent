import unittest

from calculator import add


class CalculatorTests(unittest.TestCase):
    def test_add_two_positive_numbers(self) -> None:
        self.assertEqual(add(2, 3), 5)
