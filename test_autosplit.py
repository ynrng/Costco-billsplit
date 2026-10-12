import unittest

from autosplit import extract_items, is_product, parse_total


def observation(text, x, y, width=0.3, height=0.02):
    return dict(text=text, x=x, y=y, width=width, height=height, confidence=1.0)


class ReceiptTests(unittest.TestCase):
    def test_signed_amounts_and_categories(self):
        for raw, expected in [
            ("2.50-Z", ("-2.50", "Z")),
            ("-2.50 Z", ("-2.50", "Z")),
            ("2.80 - A", ("-2.80", "A")),
            ("−4,00 Z", ("-4.00", "Z")),
            ("14.99 А", ("14.99", "A")),  # Cyrillic OCR lookalike
            ("4.89 L", ("4.89", "")),  # flag, do not guess a category
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(parse_total(raw), expected)
        self.assertIsNone(parse_total("4.999 Z"))

    def test_final_price_not_unit_price_and_preserve_repeats(self):
        raw = [
            observation("A+Y APPLES", .20, .10),
            observation("12345 2x 3.00", .18, .13),
            observation("6.00 Z", .78, .13, .16),
            observation("TOTAL NUMBER OF ITEMS SOLD = 2", .20, .16),
            observation("APPLES", .20, .19),
            observation("12345 1x 3.00", .18, .22),
            observation("3.00", .78, .22, .08),
            observation("-", .87, .22, .02),
            observation("Z", .91, .22, .02),
            observation("*** BOB Count 5 ***", .20, .25),
        ]
        items, issues = extract_items("receipt.jpeg", raw)
        self.assertEqual([i.csv_row() for i in items], [
            {"product_name": "APPLES", "total_price": "6.00", "category": "Z"},
            {"product_name": "APPLES", "total_price": "-3.00", "category": "Z"},
        ])
        self.assertFalse(issues)
        self.assertTrue(all(not item.issues for item in items))

    def test_damaged_rows_are_reported_without_reusing_previous_name(self):
        raw = [observation("MILK", .2, .1), observation("1.99 Z", .78, .13, .16),
               observation("3.?9 A", .78, .19, .16),
               observation("BREAD", .2, .25), observation("2.00 Q", .78, .28, .16),
               observation("MISSING PRICE", .2, .31)]
        # Malformed totals with a recognizable decimal prefix remain in the output.
        raw[2]["text"] = "3.9? A"
        items, issues = extract_items("receipt.jpeg", raw)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[1].product_name, "")
        self.assertEqual(items[1].total_price, "")
        self.assertTrue(items[1].issues)
        self.assertEqual(items[2].category, "")
        self.assertEqual(issues[0]["product_name"], "MISSING PRICE")

    def test_product_names_can_contain_prices_and_owner_letters(self):
        self.assertTrue(is_product("RED BULL PM £1.75"))
        self.assertTrue(is_product("BABY PLUM TOMS 1KG"))
        self.assertFalse(is_product("TOTAL NUMBER OF ITEMS SOLD = 87"))
        self.assertFalse(is_product("5E 2398"))
        self.assertFalse(is_product("C937Oął 1x 5.69"))

    def test_split_name_is_joined_without_nearby_item_id(self):
        raw = [observation("CAREX", .2, .10, .1),
               observation("SOAP 6X500ML", .33, .106, .3),
               observation("24912", .19, .115, .10),
               observation("1x 7.99", .4, .135, .2),
               observation("7.99 A", .78, .135, .16)]
        items, issues = extract_items("receipt.jpeg", raw)
        self.assertEqual(items[0].product_name, "CAREX SOAP 6X500ML")
        self.assertFalse(issues)


if __name__ == "__main__":
    unittest.main()
