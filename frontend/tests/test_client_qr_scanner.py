import unittest

from pages.nicegui_portal import parse_charger_qr


class ParseChargerQRTests(unittest.TestCase):
    def test_accepts_plain_charger_id(self):
        self.assertEqual(parse_charger_qr("  CP-CHARGE-01  "), "CP-CHARGE-01")

    def test_accepts_chargecore_uri_and_json_payloads(self):
        self.assertEqual(
            parse_charger_qr("chargecore://charger/CP-01"),
            "CP-01",
        )
        self.assertEqual(
            parse_charger_qr('{"charge_point_id":"CP-02"}'),
            "CP-02",
        )

    def test_accepts_charger_id_from_qr_url(self):
        self.assertEqual(
            parse_charger_qr(
                "https://chargecore.example/scan?chargePointId=CP-03"
            ),
            "CP-03",
        )
        self.assertEqual(
            parse_charger_qr("https://chargecore.example/chargers/CP-04"),
            "CP-04",
        )

    def test_rejects_missing_or_oversized_charger_ids(self):
        self.assertIsNone(parse_charger_qr(""))
        self.assertIsNone(parse_charger_qr("x" * 51))
        self.assertIsNone(parse_charger_qr('{"unrelated":"value"}'))


if __name__ == "__main__":
    unittest.main()
