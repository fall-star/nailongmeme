from __future__ import annotations

import unittest

from production.api.auth import _hash_password, _normalise_email, _normalise_nickname, _verify_password


class AuthHelperTest(unittest.TestCase):
    def test_password_hash_round_trip(self) -> None:
        encoded = _hash_password("correct horse battery staple")
        self.assertTrue(encoded.startswith("scrypt$"))
        self.assertTrue(_verify_password("correct horse battery staple", encoded))
        self.assertFalse(_verify_password("wrong password", encoded))

    def test_nickname_accepts_unicode_and_ascii(self) -> None:
        self.assertEqual(_normalise_nickname("奶龙测试"), "奶龙测试")
        self.assertEqual(_normalise_nickname("Fallstar_01"), "Fallstar_01")

    def test_email_normalisation(self) -> None:
        self.assertEqual(_normalise_email(" User@Example.COM "), "user@example.com")


if __name__ == "__main__":
    unittest.main()
