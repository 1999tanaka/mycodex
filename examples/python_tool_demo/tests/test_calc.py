import unittest

from calc import price_with_tax, total_with_tax


class CalcTest(unittest.TestCase):
    def test_price_with_tax(self):
        self.assertEqual(price_with_tax(1000), 1100)

    def test_total_with_tax(self):
        self.assertEqual(total_with_tax([1000, 500]), 1650)


if __name__ == "__main__":
    unittest.main()
